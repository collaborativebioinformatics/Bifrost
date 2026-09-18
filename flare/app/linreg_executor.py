"""Client side of federated linear regression (FedAvg mode). Runs inside the TRE.

  linreg_init   -> adapter.run(spec) once (Safe Output + audit apply as usual);
                   keep the Gram matrix in memory; report feature moments only
  linreg_train  -> receive global β + scaling, take local gradient steps on this
                   site's own loss, return (n, β_i). Each round is audited.
The Gram matrix itself never leaves in this mode -- only p+1 model parameters.
"""
from __future__ import annotations

import traceback

from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal

from adapters import registry, safe_output
from flare.app import linreg
from spec.analysis_spec import AnalysisSpec


class LinregExecutor(Executor):
    def __init__(self):
        super().__init__()
        self._gram: dict | None = None
        self._spec: AnalysisSpec | None = None

    def execute(self, task_name: str, shareable: Shareable, fl_ctx: FLContext, abort_signal: Signal) -> Shareable:
        tre_id = fl_ctx.get_identity_name()
        try:
            if task_name == "linreg_init":
                self._spec = AnalysisSpec.model_validate(shareable["spec"])
                result = registry.load(tre_id).run(self._spec)
                if "_linreg" not in result.stats or result.stats["_linreg"].gram is None:
                    self.log_warning(fl_ctx, f"{tre_id}: nothing releasable ({result.rejected}); not joining")
                    return make_reply(ReturnCode.EXECUTION_EXCEPTION)
                self._gram = result.stats["_linreg"].gram.model_dump()
                reply = Shareable()
                reply["moments"] = linreg.moments(self._gram)
                self.log_info(fl_ctx, f"{tre_id}: joined fed_linreg with n={result.n}")
                return reply
            if task_name == "linreg_train":
                if self._gram is None:
                    return make_reply(ReturnCode.EXECUTION_EXCEPTION)
                upd = linreg.local_update(self._gram, shareable["beta"], shareable["mean"], shareable["std"],
                                          shareable["lr"], shareable["local_steps"])
                safe_output.audit_release(tre_id, self._spec, upd["n"], {"_linreg": ["beta"]},
                                          f"fedavg_round={shareable['round']}")
                reply = Shareable()
                reply["update"] = upd
                return reply
            return make_reply(ReturnCode.TASK_UNKNOWN)
        except Exception as e:
            self.log_error(fl_ctx, f"{tre_id}: {e}\n{traceback.format_exc()}")
            return make_reply(ReturnCode.EXECUTION_EXCEPTION)
