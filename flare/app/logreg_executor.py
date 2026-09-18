"""Client side of federated logistic regression (Newton-Raphson / IRLS). Runs
inside the TRE.

  logreg_init  -> adapter.run(spec) once, purely for the usual gate (project
                  allow-list, min_cell_size); no Gram matrix is cached, since
                  logistic regression needs a fresh evaluation at each beta.
  logreg_step  -> receive the current global beta, call the adapter's
                  irls_step primitive (a fresh, local-only computation inside
                  the TRE at that beta), return (n, grad, hess). Each round is
                  audited; only p+1 numbers and a (p+1)x(p+1) matrix leave.
"""
from __future__ import annotations

import traceback

from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal

from adapters import registry, safe_output
from spec.analysis_spec import AnalysisSpec


class LogregExecutor(Executor):
    def __init__(self):
        super().__init__()
        self._spec: AnalysisSpec | None = None
        self._adapter = None

    def execute(self, task_name: str, shareable: Shareable, fl_ctx: FLContext, abort_signal: Signal) -> Shareable:
        tre_id = fl_ctx.get_identity_name()
        try:
            if task_name == "logreg_init":
                self._spec = AnalysisSpec.model_validate(shareable["spec"])
                self._adapter = registry.load(tre_id)
                result = self._adapter.run(self._spec)
                if result.n == 0:
                    self.log_warning(fl_ctx, f"{tre_id}: nothing releasable ({result.rejected}); not joining")
                    return make_reply(ReturnCode.EXECUTION_EXCEPTION)
                reply = Shareable()
                reply["n"] = result.n
                self.log_info(fl_ctx, f"{tre_id}: joined fed_logreg with n={result.n}")
                return reply
            if task_name == "logreg_step":
                if self._adapter is None or self._spec is None:
                    return make_reply(ReturnCode.EXECUTION_EXCEPTION)
                features = [self._adapter.local(c) for c in self._spec.variables]
                outcome = self._adapter.local(self._spec.outcome)
                filters = self._adapter.local_filters(self._spec)
                step = self._adapter.irls_step(outcome, features, shareable["beta"], filters)
                safe_output.audit_release(tre_id, self._spec, step["n"], {"_logreg": ["grad", "hess"]},
                                          "newton_raphson_round")
                reply = Shareable()
                reply["step"] = step
                return reply
            return make_reply(ReturnCode.TASK_UNKNOWN)
        except Exception as e:
            self.log_error(fl_ctx, f"{tre_id}: {e}\n{traceback.format_exc()}")
            return make_reply(ReturnCode.EXECUTION_EXCEPTION)
