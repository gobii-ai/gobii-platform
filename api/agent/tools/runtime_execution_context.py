import contextlib
import contextvars
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolExecutionContext:
    step_id: Optional[str] = None


_tool_execution_context_var: contextvars.ContextVar[Optional[ToolExecutionContext]] = contextvars.ContextVar(
    "tool_execution_context",
    default=None,
)


def get_tool_execution_context() -> Optional[ToolExecutionContext]:
    return _tool_execution_context_var.get(None)


@contextlib.contextmanager
def tool_execution_context(*, step_id: Optional[str] = None):
    token = _tool_execution_context_var.set(ToolExecutionContext(step_id=step_id))
    try:
        yield
    finally:
        _tool_execution_context_var.reset(token)
