"""FLARE client-side executor: runs INSIDE the TRE.

The FLARE client name IS the tre_id (provisioning is generated from sites.yaml),
so the executor loads the right adapter by identity. It never touches data
itself: adapter.run() speaks the native API and applies the Safe Output filter
before anything reaches this class. The only thing returned to the server is
AggregateResult.to_dict().
"""
from __future__ import annotations

import traceback

from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal

from adapters import registry
from spec.analysis_spec import AnalysisSpec


class AdapterExecutor(Executor):
    def __init__(self, task_name: str = "analyse"):
        super().__init__()
        self.task_name = task_name

    def execute(self, task_name: str, shareable: Shareable, fl_ctx: FLContext, abort_signal: Signal) -> Shareable:
        if task_name != self.task_name:
            return make_reply(ReturnCode.TASK_UNKNOWN)
        tre_id = fl_ctx.get_identity_name()
        try:
            spec = AnalysisSpec.model_validate(shareable["spec"])
            adapter = registry.load(tre_id)
            self.log_info(fl_ctx, f"{tre_id}: {spec.analysis_type} via {adapter.name} adapter, spec {spec.spec_hash()}")
            result = adapter.run(spec)
            self.log_info(fl_ctx, f"{tre_id}: n={result.n} released={sorted(result.stats)} rejected={result.rejected}")
            reply = Shareable()
            reply["result"] = result.to_dict()
            return reply
        except Exception as e:  # a failing site must not fail the federation
            self.log_error(fl_ctx, f"{tre_id}: {e}\n{traceback.format_exc()}")
            return make_reply(ReturnCode.EXECUTION_EXCEPTION)
