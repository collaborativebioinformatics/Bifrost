"""Demo orchestration over the existing adapters, without importing FLARE.

Direct API-to-TRE access is for the hackathon. The target secure deployment
may instead run adapters inside TREs and use outbound FLARE communication.
Run one API worker with exclusive ownership of SERVER_OUT; the existing JSON
queue is not safe for simultaneous writers in other processes (including the
overseer CLI). overseer_queue.RELEASE_LOCK serializes this process's
check/record operations and overseer decisions.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import math
import os

import httpx

from adapters import registry, safe_output
from adapters.base import AggregateResult, VariableStats
from flare.app import linreg, logreg
from scripts.sites import load_sites
from server import disclosure_check, overseer_queue
from server.aggregate import combine
from spec.analysis_spec import AnalysisSpec

LOG = logging.getLogger(__name__)


def _validate_result(result: AggregateResult, tre_id: str, spec: AnalysisSpec) -> None:
    """Re-check the wire contract after the adapter ran (a buggy adapter may have
    mutated its result after construction) and that it answers THIS spec."""
    if not isinstance(result, AggregateResult):
        raise ValueError("invalid adapter result")
    checked = AggregateResult.model_validate(result.model_dump())  # schema: allow-list, finite, square Gram
    if checked.tre_id != tre_id or checked.spec_hash != spec.spec_hash():
        raise ValueError("result does not belong to this site/spec")
    expected = ({"_linreg"} if spec.analysis_type == "fed_linreg" else
                set() if spec.analysis_type == "fed_logreg" else set(spec.variables))  # fed_logreg: gate only
    if set(checked.stats) - expected or (not checked.rejected and set(checked.stats) != expected):
        raise ValueError("unexpected aggregate variables")
    if spec.analysis_type == "fed_linreg":
        gram = checked.stats.get("_linreg", VariableStats()).gram
        if gram is not None and (gram.cols != ["intercept", spec.outcome, *spec.variables] or gram.n != checked.n):
            raise ValueError("invalid Gram metadata")


def _public_reasons(reasons: list[str]) -> list[str]:
    """Only fixed codes leave the server; never echo internal reason details."""
    codes = {"min_sites", "site_suppression", "k_anon", "dominance", "differencing", "analysis"}
    return list(dict.fromkeys(
        reason.split(":", 1)[0] if reason.split(":", 1)[0] in codes else "disclosure_review_required"
        for reason in reasons
    ))


def _queued_revision(spec: AnalysisSpec, check: dict) -> dict:
    """{"revision": ...} of the result record() just queued, else {}. Call under RELEASE_LOCK,
    right after record(), so no other run of the spec can have replaced the queue entry."""
    item = overseer_queue.queued_item(spec.spec_hash()) if check["decision"] != "OK" else None
    return {"revision": item["revision"]} if item else {}


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


def _run_logreg_step(tre_id: str, spec: AnalysisSpec, beta: list[float], timeout: float):
    """One Newton-Raphson round's worth of local computation at one site.
    Mirrors _run_site's shape/error handling; called once per round, not once
    per request, since irls_step must be re-evaluated at the current beta.
    The round leaves through the same Safe Output step as the FLARE executor's."""
    adapter = None
    try:
        adapter = registry.load(tre_id)
        adapter.http.timeout = httpx.Timeout(timeout)
        features = [adapter.local(c) for c in spec.variables]
        outcome = adapter.local(spec.outcome)
        filters = adapter.local_filters(spec)
        step = adapter.irls_step(outcome, features, beta, filters)
        return safe_output.release_irls_step(tre_id, spec, step, len(beta)), None
    except safe_output.Rejected:
        return None, "rejected"
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
    if spec.analysis_type == "fed_stats":
        # every variable carries observed values, and nothing released is non-finite
        def observed(s: dict) -> bool:
            if s.get("genotype_counts"):
                return sum(s["genotype_counts"].values()) > 0
            return (s.get("count") or 0) > 0

        released = [merged["stats"].get(v) or {} for v in spec.variables]
        scalars = [x for s in released for key, x in s.items()
                   if key in ("count", "sum", "sum_sq", "mean", "var", "min", "max") and x is not None]
        return (all(observed(s) for s in released)
                and all(isinstance(x, (int, float)) and math.isfinite(x) for x in scalars))
    ols = merged["stats"].get("_linreg", {}).get("ols", {})
    coef = ols.get("coef", {})
    return bool(coef) and "error" not in ols and all(math.isfinite(v) for v in coef.values())


def _valid_logreg_statistics(merged: dict, spec: AnalysisSpec) -> bool:
    fit = merged["stats"].get("_logreg", {}).get("logreg", {})
    coef = fit.get("coef", {})
    # A beta that no Newton round ever updated is the zero start vector, not a fit.
    return bool(coef) and merged["method"]["rounds"] > 0 and all(math.isfinite(v) for v in coef.values())


def _fedavg(results: list[AggregateResult], spec: AnalysisSpec, rounds: int, local_steps: int = 1, lr: float = 1.0) -> dict:
    """Same maths as flare/app/linreg_controller.py, driven in-process: only β
    per round would leave a site; the Gram matrices stay with the results."""
    grams = [r.stats["_linreg"].gram.model_dump() for r in results if "_linreg" in r.stats and r.stats["_linreg"].gram]
    scaling = linreg.global_scaling([linreg.moments(g) for g in grams])
    beta, history = [0.0] * (len(scaling["features"]) + 1), []
    for r in range(1, rounds + 1):
        new = linreg.fedavg([linreg.local_update(g, beta, scaling["mean"], scaling["std"], lr, local_steps) for g in grams])
        delta = max(abs(a - b) for a, b in zip(new, beta))
        beta = new
        coef = dict(zip(["intercept", *scaling["features"]], linreg.unstandardise(beta, scaling["mean"], scaling["std"])))
        history.append({"round": r, "sites": len(grams), "max_delta": delta, "coef": coef})
    return {"ols": {"outcome": spec.outcome, "coef": history[-1]["coef"]}, "n": scaling["n"], "history": history}


def analyse(spec: AnalysisSpec, fedavg_rounds: int = 0) -> tuple[int, dict]:
    """Collect every site's response, then apply the shared release workflow.
    fedavg_rounds > 0 (fed_linreg only) reports the FedAvg fit instead of the exact solve."""
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
    if spec.analysis_type == "fed_linreg" and results:
        merged["method"] = {"mode": "exact"}
        if fedavg_rounds > 0 and "_linreg" in merged["stats"]:
            merged["stats"]["_linreg"] = _fedavg(results, spec, fedavg_rounds)
            merged["method"] = {"mode": "fedavg", "rounds": fedavg_rounds, "local_steps": 1, "lr": 1.0}
    spec_dict = spec.model_dump()
    with overseer_queue.RELEASE_LOCK:
        check = disclosure_check.check(merged, spec_dict, overseer_queue.release_log_path())
        # Disclosure approval alone does not mean an OLS solve was successful.
        if results and check["decision"] == "OK" and not _valid_statistics(merged, spec):
            check = {**check, "decision": "FLAGGED",
                     "reasons": [*check["reasons"], "analysis:no_valid_statistics"]}
        overseer_queue.record(spec_dict, merged, check)
        revision = _queued_revision(spec, check)

    body = {
        "spec_hash": spec.spec_hash(),
        "status": "completed" if check["decision"] == "OK" else "flagged",
        "decision": check["decision"], "reasons": _public_reasons(check["reasons"]),
        "sites_expected": expected, "sites_reported": merged["sites_reported"],
        "sites_missing": merged["sites_missing"], "sites_failed": failed,
        "coverage": merged["coverage"], **revision,
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

    spec_dict = spec.model_dump()
    sites = sorted(r.tre_id for r in gated)
    history: list[dict] = []
    rounds_seen: dict[str, dict[str, int]] = {}  # round -> {site: n} of the steps it accepted
    fit = None
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
            rounds_seen[str(r)] = {t: s["n"] for t, s in round_steps.items()}
            if delta < tol:
                break
        fit = {"outcome": spec.outcome, "coef": dict(zip(["intercept", *spec.variables], beta))}

    # The fit's sample is the sites whose rows entered at least one Newton round -- not every
    # site that passed the gate (one rejected in every round contributed nothing).
    contributed = {t for steps in rounds_seen.values() for t in steps}
    merged = combine([g for g in gated if g.tre_id in contributed], expected)
    merged["spec_hash"] = spec.spec_hash()
    merged["contributions"]["_rounds"] = rounds_seen
    if fit:
        merged["stats"] = {"_logreg": {"logreg": fit, "n": merged["n"], "history": history}}
    merged["method"] = {"mode": "newton_raphson", "rounds": len(history), "ridge": ridge, "tol": tol}
    merged["sites_failed"] = failed

    with overseer_queue.RELEASE_LOCK:
        check = disclosure_check.check(merged, spec_dict, overseer_queue.release_log_path())
        if gated and check["decision"] == "OK" and not _valid_logreg_statistics(merged, spec):
            check = {**check, "decision": "FLAGGED",
                     "reasons": [*check["reasons"], "analysis:no_valid_statistics"]}
        overseer_queue.record(spec_dict, merged, check)
        revision = _queued_revision(spec, check)

    body = {
        "spec_hash": spec.spec_hash(),
        "status": "completed" if check["decision"] == "OK" else "flagged",
        "decision": check["decision"], "reasons": _public_reasons(check["reasons"]),
        "sites_expected": expected, "sites_reported": merged["sites_reported"],
        "sites_missing": merged["sites_missing"], "sites_failed": failed,
        "coverage": merged["coverage"], **revision,
    }
    if not gated:
        return 503, {**body, "status": "failed", "error": "no_tres_responded"}
    if check["decision"] == "OK":
        body["result"] = overseer_queue._releasable(merged)
    return 200, body
