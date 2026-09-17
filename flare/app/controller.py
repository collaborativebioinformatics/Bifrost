"""FLARE server-side workflow: broadcast one AnalysisSpec, gather
AggregateResults, merge, disclosure-check, release or queue for the overseer.

Straggler-tolerant by construction: `min_clients` + `wait_time` (never "wait
for all"); sites that don't answer are listed in the result's `sites_missing`
and the coverage string ("2/3 sites"), not fatal.
"""
from __future__ import annotations

import time

from nvflare.apis.client import Client
from nvflare.apis.controller_spec import ClientTask, Task
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.impl.controller import Controller
from nvflare.apis.shareable import Shareable
from nvflare.apis.signal import Signal

from adapters.base import AggregateResult
from scripts.sites import load_sites
from server import disclosure_check, overseer_queue
from server.aggregate import combine
from spec.analysis_spec import AnalysisSpec


class FedAnalysisController(Controller):
    def __init__(self, spec: dict, min_clients: int = 2, wait_time: int = 60, task_timeout: int = 300,
                 task_name: str = "analyse"):
        super().__init__()
        self.spec = AnalysisSpec.model_validate(spec)  # validate early: a bad spec fails the job before any client runs
        self.min_clients = min_clients
        self.wait_time = wait_time
        self.task_timeout = task_timeout
        self.task_name = task_name
        self.results: dict[str, AggregateResult] = {}
        self.failed: dict[str, str] = {}

    def start_controller(self, fl_ctx: FLContext):
        self.log_info(fl_ctx, f"spec {self.spec.spec_hash()} ({self.spec.analysis_type}) min_clients={self.min_clients} wait_time={self.wait_time}s")

    def _collect(self, client_task: ClientTask, fl_ctx: FLContext):
        name, rc = client_task.client.name, client_task.result.get_return_code()
        if rc == ReturnCode.OK and "result" in client_task.result:
            self.results[name] = AggregateResult.from_dict(client_task.result["result"])
            self.log_info(fl_ctx, f"result from {name}: n={self.results[name].n}")
        else:
            self.failed[name] = str(rc)
            self.log_warning(fl_ctx, f"{name} failed: {rc}")
        client_task.result = None  # free memory; nothing else to do with it

    def control_flow(self, abort_signal: Signal, fl_ctx: FLContext):
        data = Shareable()
        data["spec"] = self.spec.model_dump()
        task = Task(name=self.task_name, data=data, timeout=self.task_timeout, result_received_cb=self._collect)
        t0 = time.time()
        self.broadcast_and_wait(task, fl_ctx, min_responses=self.min_clients,
                                wait_time_after_min_received=self.wait_time, abort_signal=abort_signal)
        if abort_signal.triggered:
            return

        # the federation is what sites.yaml says, not who happens to be connected right now:
        # a site whose client is down still counts as missing ("2/3 sites"), never silently 2/2
        expected = sorted(s["tre_id"] for s in load_sites()["sites"])
        merged = combine(self.results.values(), expected)
        merged["sites_failed"] = self.failed
        merged["timing"] = {"round_s": round(time.time() - t0, 3), "merge_s": None}
        t1 = time.time()
        check = disclosure_check.check(merged, self.spec.model_dump(), overseer_queue.release_log_path())
        merged["timing"]["merge_s"] = round(time.time() - t1, 3)
        run_dir = overseer_queue.record(self.spec.model_dump(), merged, check)
        self.log_info(fl_ctx, f"{merged['coverage']} (missing {merged['sites_missing']}), n={merged['n']}, "
                              f"disclosure check {check['decision']} {check['reasons']} -> {run_dir}")
        fl_ctx.set_prop("fed_analysis_result", merged, private=True, sticky=True)

    def stop_controller(self, fl_ctx: FLContext):
        pass

    def process_result_of_unknown_task(self, client: Client, task_name: str, client_task_id: str, result: Shareable, fl_ctx: FLContext):
        self.log_warning(fl_ctx, f"unknown task result from {client.name}: {task_name}")
