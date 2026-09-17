"""Server side of federated logistic regression (Newton-Raphson / IRLS). See
flare/app/logreg.py for the maths.

Straggler-tolerant like the analysis and linreg controllers: every round uses
min_clients + wait_time; the set of participating sites is fixed at init
(a site that misses a training round is dropped from that round's sum and
reported in `sites_missing_rounds`).
"""
from __future__ import annotations

from nvflare.apis.client import Client
from nvflare.apis.controller_spec import ClientTask, Task
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Controller
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal

from flare.app import logreg
from scripts.sites import load_sites
from server import disclosure_check, overseer_queue
from spec.analysis_spec import AnalysisSpec


class FedLogregController(Controller):
    def __init__(self, spec: dict, rounds: int = 25, tol: float = 1e-8, ridge: float = 1e-6,
                 min_clients: int = 2, wait_time: int = 60, task_timeout: int = 300):
        super().__init__()
        self.spec = AnalysisSpec.model_validate(spec)
        if self.spec.analysis_type != "fed_logreg":
            raise ValueError("FedLogregController needs a fed_logreg spec")
        self.rounds, self.tol, self.ridge = rounds, tol, ridge
        self.min_clients, self.wait_time, self.task_timeout = min_clients, wait_time, task_timeout
        self._replies: dict[str, Shareable] = {}

    def start_controller(self, fl_ctx: FLContext):
        self.log_info(fl_ctx, f"fed_logreg Newton-Raphson: rounds<={self.rounds} tol={self.tol}")

    def _collect(self, client_task: ClientTask, fl_ctx: FLContext):
        if client_task.result.get_return_code() == ReturnCode.OK:
            self._replies[client_task.client.name] = client_task.result
        else:
            self.log_warning(fl_ctx, f"{client_task.client.name}: {client_task.result.get_return_code()}")
        client_task.result = None

    def _round(self, name: str, data: Shareable, fl_ctx: FLContext, abort_signal: Signal, targets=None) -> dict[str, Shareable]:
        self._replies = {}
        task = Task(name=name, data=data, timeout=self.task_timeout, result_received_cb=self._collect)
        self.broadcast_and_wait(task, fl_ctx, targets=targets, min_responses=self.min_clients,
                                wait_time_after_min_received=self.wait_time, abort_signal=abort_signal)
        return dict(self._replies)

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext):
        # the federation is what sites.yaml says, not who happens to be connected right now:
        # a site whose client is down still counts as missing ("2/3 sites"), never silently 2/2
        expected = sorted(s["tre_id"] for s in load_sites()["sites"])
        init = Shareable()
        init["spec"] = self.spec.model_dump()
        replies = self._round("logreg_init", init, fl_ctx, abort_signal)
        if abort_signal.triggered or len(replies) < self.min_clients:
            self.log_error(fl_ctx, f"only {len(replies)} sites joined (< {self.min_clients}); aborting")
            return

        sites = sorted(replies)
        n_per_site = {s: replies[s]["n"] for s in sites}
        features = self.spec.variables
        beta = [0.0] * (len(features) + 1)
        history, missing_rounds = [], {}

        for r in range(1, self.rounds + 1):
            data = Shareable()
            data["beta"] = beta
            replies = self._round("logreg_step", data, fl_ctx, abort_signal, targets=sites)
            if abort_signal.triggered:
                return
            if len(replies) < self.min_clients:
                self.log_error(fl_ctx, f"round {r}: only {len(replies)} updates; stopping early")
                break
            miss = [s for s in sites if s not in replies]
            if miss:
                missing_rounds[r] = miss

            n, grad, hess = logreg.combine([rep["step"] for rep in replies.values()])
            new_beta = logreg.newton_update(beta, grad, hess, ridge=self.ridge)
            delta = logreg.max_abs_delta(beta, new_beta)
            beta = new_beta
            history.append({"round": r, "sites": len(replies), "max_delta": delta,
                            "coef": dict(zip(["intercept", *features], beta))})
            self.log_info(fl_ctx, f"round {r}/{self.rounds}: {len(replies)} sites, max|Δβ|={delta:.2e}")
            if delta < self.tol:
                break

        merged = {
            "sites_expected": expected, "sites_reported": sites, "sites_missing": [s for s in expected if s not in sites],
            "coverage": f"{len(sites)}/{len(expected)} sites", "n": sum(n_per_site.values()), "n_per_site": n_per_site,
            "rejected_per_site": {}, "spec_hash": self.spec.spec_hash(), "sites_missing_rounds": missing_rounds,
            "method": {"mode": "newton_raphson", "rounds": len(history), "ridge": self.ridge},
            "stats": {"_logreg": {"logreg": {"outcome": self.spec.outcome,
                                              "coef": dict(zip(["intercept", *features], beta))},
                                  "n": sum(n_per_site.values()), "history": history}},
            "contributions": {},
        }
        check = disclosure_check.check(merged, self.spec.model_dump(), overseer_queue.release_log_path())
        run_dir = overseer_queue.record(self.spec.model_dump(), merged, check)
        self.log_info(fl_ctx, f"{merged['coverage']}, n={merged['n']}, check {check['decision']} {check['reasons']} -> {run_dir}")

    def stop_controller(self, fl_ctx: FLContext):
        pass

    def process_result_of_unknown_task(self, client: Client, task_name: str, client_task_id: str, result: Shareable, fl_ctx: FLContext):
        self.log_warning(fl_ctx, f"unknown task result from {client.name}: {task_name}")
