"""Demo orchestration over the existing adapters, without importing FLARE.

Direct API-to-TRE access is for the hackathon. The target secure deployment
may instead run adapters inside TREs and use outbound FLARE communication.
Run one API worker with exclusive ownership of SERVER_OUT; the existing JSON
queue is not safe for simultaneous writers in other processes (including the
overseer CLI). The lock below serializes this process's check/record operations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
from threading import Lock

import httpx

from adapters import registry
from adapters.base import AggregateResult
from flare.app import logreg
from scripts.sites import load_sites
from server import disclosure_check, overseer_queue
from server.aggregate import combine
from spec.analysis_spec import AnalysisSpec

LOG = logging.getLogger(__name__)
_RELEASE_LOCK = Lock()


def _validate_result(result: AggregateResult, tre_id: str, spec: AnalysisSpec) -> None:
    """Validate the aggregate contract per site, before the shared merge."""
    def count(value):
        return type(value) is int and value >= 0

    if (not isinstance(result, AggregateResult) or result.tre_id != tre_id
            or not count(result.n) or not isinstance(result.stats, dict)
            or not isinstance(result.rejected, list)
            or not all(isinstance(reason, str) for reason in result.rejected)
            or not isinstance(result.region, str) or result.spec_hash != spec.spec_hash()):
        raise ValueError("invalid adapter result")
    json.dumps(result.to_dict(), allow_nan=False)
    expected = ({"_linreg"} if spec.analysis_type == "fed_linreg" else
                set() if spec.analysis_type == "fed_logreg" else set(spec.variables))
    if set(result.stats) - expected:
        raise ValueError("unexpected aggregate variables")
    if not result.rejected and set(result.stats) != expected:
        raise ValueError("missing aggregate variables")
    for stats in result.stats.values():
        if not isinstance(stats, dict):
            raise ValueError("invalid aggregate statistics")
        if spec.analysis_type == "fed_linreg":
            if set(stats) - {"gram"} or ("gram" not in stats and not result.rejected):
                raise ValueError("invalid regression statistics")
            if "gram" not in stats:  # legitimately withheld by the site's filter
                continue
            gram = stats["gram"]
            cols = ["intercept", spec.outcome, *spec.variables]
            if (not isinstance(gram, dict) or gram.get("cols") != cols
                    or not count(gram.get("n")) or gram["n"] != result.n):
                raise ValueError("invalid Gram metadata")
            matrix = gram.get("matrix")
            if (not isinstance(matrix, list) or len(matrix) != len(cols)
                    or any(not isinstance(row, list) or len(row) != len(cols)
                           or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
                           for row in matrix)):
                raise ValueError("invalid Gram matrix")
        else:
            if set(stats) - {"genotype_counts", "allele_counts"}:
                raise ValueError("invalid allele statistics")
            table = stats.get("genotype_counts")
            if (not isinstance(table, dict) or set(table) - {"0", "1", "2"}
                    or not all(count(v) for v in table.values())):
                raise ValueError("invalid genotype counts")
            alleles = stats.get("allele_counts")
            if alleles is None and result.rejected:
                continue
            if (not isinstance(alleles, dict) or set(alleles) != {"minor", "major", "n_alleles"}
                    or not all(count(v) for v in alleles.values())):
                raise ValueError("invalid allele counts")


def _public_reasons(reasons: list[str]) -> list[str]:
    """Only fixed codes leave the server; never echo internal reason details."""
    codes = {"min_sites", "site_suppression", "k_anon", "dominance", "differencing", "analysis"}
    return list(dict.fromkeys(
        reason.split(":", 1)[0] if reason.split(":", 1)[0] in codes else "disclosure_review_required"
        for reason in reasons
    ))


def _run_site(tre_id: str, spec: AnalysisSpec, timeout: float):
    adapter = None
    try:
        adapter = registry.load(tre_id)
        adapter.http.timeout = httpx.Timeout(timeout)
        result = adapter.run(spec)
        _validate_result(result, tre_id, spec)
        return result, None
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.RequestError:
        return None, "unavailable"
    except Exception:
        LOG.warning("TRE %s failed", tre_id, exc_info=True)
        return None, "adapter_error"
    finally:
        if adapter is not None:
            try:
                adapter.http.close()
            except Exception:
                LOG.warning("Could not close TRE %s client", tre_id)


def _validate_irls_step(step, p: int) -> None:
    """Same spirit as _validate_result's Gram check, for one round's worth of
    logistic-regression sufficient statistics from one site."""
    def count(value):
        return type(value) is int and value >= 0

    if not isinstance(step, dict) or not count(step.get("n")):
        raise ValueError("invalid irls_step result")
    json.dumps(step, allow_nan=False)
    grad, hess = step.get("grad"), step.get("hess")
    if (not isinstance(grad, list) or len(grad) != p
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in grad)):
        raise ValueError("invalid irls_step gradient")
    if (not isinstance(hess, list) or len(hess) != p
            or any(not isinstance(row, list) or len(row) != p
                   or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
                   for row in hess)):
        raise ValueError("invalid irls_step Hessian")


def _run_logreg_step(tre_id: str, spec: AnalysisSpec, beta: list[float], timeout: float):
    """One Newton-Raphson round's worth of local computation at one site.
    Mirrors _run_site's shape/error handling; called once per round, not once
    per request, since irls_step must be re-evaluated at the current beta."""
    adapter = None
    try:
        adapter = registry.load(tre_id)
        adapter.http.timeout = httpx.Timeout(timeout)
        features = [adapter.local(c) for c in spec.variables]
        outcome = adapter.local(spec.outcome)
        filters = adapter.local_filters(spec)
        step = adapter.irls_step(outcome, features, beta, filters)
        _validate_irls_step(step, len(beta))
        return step, None
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.RequestError:
        return None, "unavailable"
    except Exception:
        LOG.warning("TRE %s failed (logreg round)", tre_id, exc_info=True)
        return None, "adapter_error"
    finally:
        if adapter is not None:
            try:
                adapter.http.close()
            except Exception:
                LOG.warning("Could not close TRE %s client", tre_id)


def _valid_statistics(merged: dict, spec: AnalysisSpec) -> bool:
    if spec.analysis_type == "allele_freq":
        value = merged["stats"].get(spec.variables[0], {}).get("allele_freq")
        return isinstance(value, (int, float)) and math.isfinite(value)
    ols = merged["stats"].get("_linreg", {}).get("ols", {})
    coef = ols.get("coef", {})
    return bool(coef) and "error" not in ols and all(math.isfinite(v) for v in coef.values())


def _valid_logreg_statistics(merged: dict, spec: AnalysisSpec) -> bool:
    fit = merged["stats"].get("_logreg", {}).get("logreg", {})
    coef = fit.get("coef", {})
    # A beta that no Newton round ever updated is the zero start vector, not a fit.
    return bool(coef) and merged["method"]["rounds"] > 0 and all(math.isfinite(v) for v in coef.values())


def analyse(spec: AnalysisSpec) -> tuple[int, dict]:
    """Collect every site's response, then apply the shared release workflow."""
    expected = [s["tre_id"] for s in load_sites()["sites"]]
    timeout = float(os.environ.get("API_TRE_TIMEOUT", "10"))
    workers = int(os.environ.get("API_TRE_WORKERS", "8"))
    if not math.isfinite(timeout) or timeout <= 0 or workers < 1:
        raise ValueError("Invalid API timeout/worker configuration")
    results, failed = [], {}
    if expected:
        with ThreadPoolExecutor(max_workers=min(workers, len(expected))) as pool:
            futures = {tid: pool.submit(_run_site, tid, spec, timeout) for tid in expected}
            for tid, future in futures.items():
                result, error = future.result()
                if error:
                    failed[tid] = error
                else:
                    results.append(result)

    merged = combine(results, expected)
    merged["spec_hash"] = spec.spec_hash()  # also identify zero-response attempts
    merged["sites_failed"] = failed
    spec_dict = spec.model_dump()
    with _RELEASE_LOCK:
        check = disclosure_check.check(merged, spec_dict, overseer_queue.release_log_path())
        # Disclosure approval alone does not mean an OLS solve was successful.
        if results and check["decision"] == "OK" and not _valid_statistics(merged, spec):
            check = {**check, "decision": "FLAGGED",
                     "reasons": [*check["reasons"], "analysis:no_valid_statistics"]}
        overseer_queue.record(spec_dict, merged, check)

    body = {
        "spec_hash": spec.spec_hash(),
        "status": "completed" if check["decision"] == "OK" else "flagged",
        "decision": check["decision"], "reasons": _public_reasons(check["reasons"]),
        "sites_expected": expected, "sites_reported": merged["sites_reported"],
        "sites_missing": merged["sites_missing"], "sites_failed": failed,
        "coverage": merged["coverage"],
    }
    if not results:
        return 503, {**body, "status": "failed", "error": "no_tres_responded"}
    if check["decision"] == "OK":
        # Always use this request's in-memory result, never stale released.json.
        body["result"] = overseer_queue._releasable(merged)
    return 200, body


def analyse_logreg(spec: AnalysisSpec, rounds: int = 25, tol: float = 1e-8, ridge: float = 1e-6) -> tuple[int, dict]:
    """Federated logistic regression via distributed Newton-Raphson (IRLS), without FLARE:
    the server calls every gated site once per round in the same thread pool `analyse()`
    uses for one-shot analyses. A request with R rounds and S sites makes on the order of
    R x S sequential-per-round (parallel-per-site) HTTP calls -- fine for a handful of TREs
    and a few dozen rounds, not a fit for a slow/large federation. Shares the disclosure
    check, overseer queue and public response shape with `analyse()`.
    """
    expected = [s["tre_id"] for s in load_sites()["sites"]]
    timeout = float(os.environ.get("API_TRE_TIMEOUT", "10"))
    workers = int(os.environ.get("API_TRE_WORKERS", "8"))
    if not math.isfinite(timeout) or timeout <= 0 or workers < 1:
        raise ValueError("Invalid API timeout/worker configuration")

    # Round 0: gate sites in with the same project/min-cell-size checks every analysis uses.
    gated, failed = [], {}
    if expected:
        with ThreadPoolExecutor(max_workers=min(workers, len(expected))) as pool:
            futures = {tid: pool.submit(_run_site, tid, spec, timeout) for tid in expected}
            for tid, future in futures.items():
                result, error = future.result()
                if error:
                    failed[tid] = error
                elif result.n == 0:
                    failed[tid] = "rejected"  # e.g. cohort below min_cell_size at this site
                else:
                    gated.append(result)

    merged = combine(gated, expected)
    merged["spec_hash"] = spec.spec_hash()
    spec_dict = spec.model_dump()

    sites = sorted(r.tre_id for r in gated)
    history: list[dict] = []
    if len(sites) >= 2:  # matches disclosure_check's min_sites floor; no point running rounds otherwise
        beta = [0.0] * (len(spec.variables) + 1)
        for r in range(1, rounds + 1):
            with ThreadPoolExecutor(max_workers=min(workers, len(sites))) as pool:
                futures = {tid: pool.submit(_run_logreg_step, tid, spec, beta, timeout) for tid in sites}
                round_steps, round_failed = {}, {}
                for tid, future in futures.items():
                    step, error = future.result()
                    if error:
                        round_failed[tid] = error
                    else:
                        round_steps[tid] = step
            for tid, err in round_failed.items():
                failed.setdefault(tid, err)  # first failure reason wins; a site can recover next round
            if len(round_steps) < 2:
                break  # too few sites answered this round; keep the last beta we had
            n, grad, hess = logreg.combine(list(round_steps.values()))
            new_beta = logreg.newton_update(beta, grad, hess, ridge=ridge)
            delta = logreg.max_abs_delta(beta, new_beta)
            beta = new_beta
            history.append({"round": r, "sites": len(round_steps), "max_delta": delta,
                            "coef": dict(zip(["intercept", *spec.variables], beta))})
            if delta < tol:
                break
        merged["stats"] = {"_logreg": {"logreg": {"outcome": spec.outcome,
                                                   "coef": dict(zip(["intercept", *spec.variables], beta))},
                                       "n": merged["n"], "history": history}}
    merged["method"] = {"mode": "newton_raphson", "rounds": len(history), "ridge": ridge}
    merged["sites_failed"] = failed

    with _RELEASE_LOCK:
        check = disclosure_check.check(merged, spec_dict, overseer_queue.release_log_path())
        if gated and check["decision"] == "OK" and not _valid_logreg_statistics(merged, spec):
            check = {**check, "decision": "FLAGGED",
                     "reasons": [*check["reasons"], "analysis:no_valid_statistics"]}
        overseer_queue.record(spec_dict, merged, check)

    body = {
        "spec_hash": spec.spec_hash(),
        "status": "completed" if check["decision"] == "OK" else "flagged",
        "decision": check["decision"], "reasons": _public_reasons(check["reasons"]),
        "sites_expected": expected, "sites_reported": merged["sites_reported"],
        "sites_missing": merged["sites_missing"], "sites_failed": failed,
        "coverage": merged["coverage"],
    }
    if not gated:
        return 503, {**body, "status": "failed", "error": "no_tres_responded"}
    if check["decision"] == "OK":
        body["result"] = overseer_queue._releasable(merged)
    return 200, body
