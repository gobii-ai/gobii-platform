"""
Tool search orchestration for persistent agents.

Provides a unified `search_tools` function that queries the LLM once across
both MCP-discovered tools and builtin tools, then enables any selected tools.
"""

import json
import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

import litellm  # re-exported for tests that patch LiteLLM directly
from opentelemetry import trace

from ...models import PersistentAgent
from ..core.llm_config import LLMNotConfiguredError, get_llm_config_with_failover
from ..core.llm_utils import run_completion
from .mcp_manager import get_mcp_manager
from .tool_manager import enable_tools, BUILTIN_TOOL_REGISTRY

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

ToolSearchResult = Dict[str, Any]


def _strip_description(text: str, limit: int = 180) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit].rstrip() + ("…" if len(cleaned) > limit else "")


def _summarize_parameters(schema: Dict[str, Any], limit: int = 6) -> str:
    try:
        if not isinstance(schema, dict):
            return ""
        props = schema.get("properties") or {}
        if not isinstance(props, dict) or not props:
            return ""
        required = set(schema.get("required") or [])
        items: List[str] = []
        for idx, (key, value) in enumerate(props.items()):
            if idx >= limit:
                items.append(f"+{len(props) - limit} more")
                break
            param_type = value.get("type") if isinstance(value, dict) else None
            param_type = param_type if isinstance(param_type, str) else "any"
            suffix = "*" if key in required else ""
            items.append(f"{key}{suffix}:{param_type}")
        return ", ".join(items)
    except Exception:  # pragma: no cover - defensive safety
        return ""


def _tool_attr(tool: Any, attr: str, default: Any = None) -> Any:
    if hasattr(tool, attr):
        return getattr(tool, attr)
    if isinstance(tool, dict):
        return tool.get(attr, default)
    return default


def _search_with_llm(
    agent: PersistentAgent,
    query: str,
    provider_name: str,
    catalog: Iterable[Any],
    enable_callback: Callable[[PersistentAgent, List[str]], Dict[str, Any]],
    empty_message: str,
) -> ToolSearchResult:
    tools = list(catalog)
    logger.info("search_tools.%s: %d tools available", provider_name, len(tools))

    if not tools:
        return {"status": "success", "tools": [], "message": empty_message}

    tool_lines: List[str] = []
    for tool in tools:
        full_name = _tool_attr(tool, "full_name") or _tool_attr(tool, "name")
        description = _tool_attr(tool, "description", "")
        parameters = _tool_attr(tool, "parameters", {})
        line = f"- {full_name}"
        desc = _strip_description(description or "")
        if desc:
            line += f": {desc}"
        params_summary = _summarize_parameters(parameters or {})
        if params_summary:
            line += f" | params: {params_summary}"
        tool_lines.append(line)

    try:
        preview = "\n".join(tool_lines[:5])
        logger.info(
            "search_tools.%s: compact catalog prepared with %d entries; first few:\n%s",
            provider_name,
            len(tool_lines),
            preview,
        )
        if len(tool_lines) > 5:
            logger.info(
                "search_tools.%s: (truncated catalog log; total entries=%d)",
                provider_name,
                len(tool_lines),
            )
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("search_tools.%s: failed to log compact catalog preview", provider_name)

    system_prompt = (
        "You are a concise tool discovery assistant. Given a user query and a list of available tools "
        "(names, brief descriptions, and summarized parameters), you MUST select ALL relevant tools and then "
        "call the function enable_tools exactly once with the full tool names you selected. "
        "If no tools are relevant, do not call the function and reply briefly explaining that none are relevant."
    )
    user_prompt = (
        f"Query: {query}\n\n"
        "Available tools (names and brief details):\n"
        + "\n".join(tool_lines)
        + "\n\nSelect the relevant tools and call enable_tools once with their exact full names."
    )

    try:
        failover_configs = get_llm_config_with_failover()
        last_exc: Optional[Exception] = None
        for idx, (provider, model, params) in enumerate(failover_configs):
            try:
                logger.info(
                    "search_tools.%s: invoking provider %s/%s: provider=%s model=%s",
                    provider_name,
                    idx + 1,
                    len(failover_configs),
                    provider,
                    model,
                )
                enable_tools_def = {
                    "type": "function",
                    "function": {
                        "name": "enable_tools",
                        "description": (
                            "Enable multiple tools in one call. Provide the exact full names "
                            "from the catalog above."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "tool_names": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                    "uniqueItems": True,
                                    "description": "List of full tool names to enable",
                                }
                            },
                            "required": ["tool_names"],
                        },
                    },
                }

                run_kwargs: Dict[str, Any] = {}
                safety_value = getattr(agent.user, "id", None) if agent and agent.user else None
                if (
                    safety_value is not None
                    and isinstance(provider, str)
                    and provider.lower().startswith("openai")
                ):
                    run_kwargs["safety_identifier"] = str(safety_value)

                response = run_completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    params=params,
                    tools=[enable_tools_def],
                    **run_kwargs,
                )

                message = response.choices[0].message
                content_text = getattr(message, "content", None) or ""

                requested: List[str] = []
                tool_calls = getattr(message, "tool_calls", None) or []
                for tool_call in tool_calls:
                    try:
                        if not tool_call:
                            continue
                        function_block = getattr(tool_call, "function", None) or tool_call.get("function")
                        if not function_block:
                            continue
                        function_name = getattr(function_block, "name", None) or function_block.get("name")
                        if function_name != "enable_tools":
                            continue
                        raw_args = getattr(function_block, "arguments", None) or function_block.get("arguments") or "{}"
                        arguments = json.loads(raw_args)
                        names = arguments.get("tool_names") or []
                        if isinstance(names, list):
                            for name in names:
                                if isinstance(name, str) and name not in requested:
                                    requested.append(name)
                    except Exception:  # pragma: no cover - defensive parsing
                        logger.exception("search_tools.%s: failed to parse tool call; skipping", provider_name)

                enabled_result = None
                if requested:
                    try:
                        enabled_result = enable_callback(agent, requested)
                    except Exception as err:  # pragma: no cover - defensive enabling
                        logger.error("search_tools.%s: enable_tools failed: %s", provider_name, err)

                message_lines: List[str] = []
                if content_text:
                    message_lines.append(content_text.strip())
                if enabled_result and enabled_result.get("status") == "success":
                    summary: List[str] = []
                    if enabled_result.get("enabled"):
                        summary.append(f"Enabled: {', '.join(enabled_result['enabled'])}")
                    if enabled_result.get("already_enabled"):
                        summary.append(f"Already enabled: {', '.join(enabled_result['already_enabled'])}")
                    if enabled_result.get("evicted"):
                        summary.append(f"Evicted (LRU): {', '.join(enabled_result['evicted'])}")
                    if enabled_result.get("invalid"):
                        summary.append(f"Invalid: {', '.join(enabled_result['invalid'])}")
                    if summary:
                        message_lines.append("; ".join(summary))

                response_payload: ToolSearchResult = {
                    "status": "success",
                    "message": "\n".join([line for line in message_lines if line]) or "",
                }
                if enabled_result and enabled_result.get("status") == "success":
                    response_payload.update(
                        {
                            "enabled_tools": enabled_result.get("enabled", []),
                            "already_enabled": enabled_result.get("already_enabled", []),
                            "evicted": enabled_result.get("evicted", []),
                            "invalid": enabled_result.get("invalid", []),
                        }
                    )
                return response_payload

            except Exception as exc:  # pragma: no cover - failover loop
                last_exc = exc
                logger.warning(
                    "search_tools.%s: provider %s failed for tool search: %s",
                    provider_name,
                    provider,
                    exc,
                )
                continue

        logger.error("search_tools.%s: all providers failed for tool search: %s", provider_name, last_exc)
        return {"status": "error", "message": "Failed to search tools"}

    except LLMNotConfiguredError:
        logger.warning("search_tools.%s: skipped because LLM configuration is missing", provider_name)
        return {
            "status": "error",
            "message": "Tool search is unavailable until the initial LLM setup is complete.",
            "reason": "llm_not_configured",
        }
    except Exception as exc:  # pragma: no cover - top-level guard
        logger.error("search_tools.%s: unexpected error during search: %s", provider_name, exc)
        return {"status": "error", "message": str(exc)}

def search_tools(agent: PersistentAgent, query: str) -> ToolSearchResult:
    """Search across MCP and builtin tools in a single LLM call."""
    manager = get_mcp_manager()
    if not manager._initialized:
        manager.initialize()

    mcp_tools = manager.get_tools_for_agent(agent)

    builtin_catalog: List[Dict[str, Any]] = []
    for name, registry_entry in BUILTIN_TOOL_REGISTRY.items():
        try:
            tool_def = registry_entry["definition"]()
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("search_tools: failed to build builtin tool definition for %s", name)
            continue
        function_block = tool_def.get("function") if isinstance(tool_def, dict) else {}
        builtin_catalog.append(
            {
                "full_name": function_block.get("name", name),
                "description": function_block.get("description", ""),
                "parameters": function_block.get("parameters", {}),
            }
        )

    combined_catalog: List[Any] = list(mcp_tools) + builtin_catalog

    if not combined_catalog:
        logger.info("search_tools: no tools available for agent %s", agent.id)
        return {"status": "success", "tools": [], "message": "No tools available"}

    return _search_with_llm(
        agent=agent,
        query=query,
        provider_name="catalog",
        catalog=combined_catalog,
        enable_callback=enable_tools,
        empty_message="No tools available",
    )


def get_search_tools_tool() -> Dict[str, Any]:
    """Return the search_tools tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "search_tools",
            "description": (
                "Search for available tools relevant to a query. "
                "This call will automatically enable all relevant tools in one step when supported."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Description of what you want to accomplish or what kind of tools you're looking for",
                    }
                },
                "required": ["query"],
            },
        },
    }


@tracer.start_as_current_span("AGENT TOOL Search Tools")
def execute_search_tools(agent: PersistentAgent, params: Dict[str, Any]) -> ToolSearchResult:
    """Execute the search_tools function to find relevant tools."""
    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))

    query = params.get("query")
    if not query:
        return {"status": "error", "message": "Missing required parameter: query"}

    span.set_attribute("search.query", query)
    logger.info("Agent %s searching for tools: %s", agent.id, query)

    return search_tools(agent, query)
