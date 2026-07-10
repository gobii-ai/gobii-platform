import contextlib
import contextvars
from dataclasses import dataclass
from typing import Any, Optional


_UNBOUND = object()


@dataclass(frozen=True)
class ToolExecutionContext:
    step_id: Optional[str] = None
    requester_config_authority: Optional[bool] = None
    requester_config_authority_bound: bool = False


_tool_execution_context_var: contextvars.ContextVar[Optional[ToolExecutionContext]] = contextvars.ContextVar(
    "tool_execution_context",
    default=None,
)


def get_tool_execution_context() -> Optional[ToolExecutionContext]:
    return _tool_execution_context_var.get(None)


def resolve_requester_config_authority(agent: Any) -> Optional[bool]:
    """Return the authority captured for this tool turn, falling back for direct calls."""
    context = get_tool_execution_context()
    if context is not None and context.requester_config_authority_bound:
        return context.requester_config_authority

    from api.agent.core.prompt_context import get_active_requester_config_authority

    return get_active_requester_config_authority(agent)


@contextlib.contextmanager
def tool_execution_context(
    *,
    step_id: Optional[str] = None,
    requester_config_authority: object = _UNBOUND,
):
    parent = get_tool_execution_context()
    if requester_config_authority is _UNBOUND and parent is not None and parent.requester_config_authority_bound:
        requester_config_authority = parent.requester_config_authority
    authority_bound = requester_config_authority is not _UNBOUND
    bound_authority = requester_config_authority if authority_bound else None

    token = _tool_execution_context_var.set(
        ToolExecutionContext(
            step_id=step_id,
            requester_config_authority=bound_authority,
            requester_config_authority_bound=authority_bound,
        )
    )
    try:
        yield
    finally:
        _tool_execution_context_var.reset(token)
