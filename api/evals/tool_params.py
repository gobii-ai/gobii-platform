from typing import Any

from api.agent.core.link_references import LinkReferenceResolutionError, resolve_link_references


def resolved_tool_param(call, key: str) -> Any:
    value = (getattr(call, "tool_params", None) or {}).get(key)
    if not isinstance(value, str):
        return value
    agent = getattr(getattr(call, "step", None), "agent", None)
    if agent is None:
        return value
    try:
        return resolve_link_references(value, agent)
    except LinkReferenceResolutionError:
        return value
