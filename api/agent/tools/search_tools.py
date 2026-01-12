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
from litellm import drop_params
from opentelemetry import trace

from ...models import PersistentAgent, PersistentAgentCompletion
from ...evals.execution import get_current_eval_routing_profile
from ..core.llm_config import LLMNotConfiguredError, get_llm_config_with_failover
from ..core.llm_utils import run_completion
from ..core.token_usage import log_agent_completion, set_usage_span_attributes
from .mcp_manager import get_mcp_manager
from .tool_manager import (
    enable_tools,
    BUILTIN_TOOL_REGISTRY,
    HTTP_REQUEST_TOOL_NAME,
    get_enabled_tool_limit,
)
from .autotool_heuristics import find_matching_tools

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


def _fallback_builtin_selection(
    query: str,
    content_text: str,
    available_names: set[str],
) -> list[str]:
    """
    Heuristically select tools when the LLM response does not call enable_tools.

    Uses keyword matching from autotool_heuristics for MCP tools (LinkedIn, Crunchbase, etc.)
    and basic keyword matching for builtins like http_request.
    """
    text = f"{query} {content_text}".lower()
    candidates: list[str] = []

    # Use autotool heuristics to find matching MCP tools (linkedin, crunchbase, etc.)
    heuristic_matches = find_matching_tools(text)
    for tool_name in heuristic_matches:
        if tool_name in available_names and tool_name not in candidates:
            candidates.append(tool_name)

    # Also check for API/http keywords for http_request
    wants_api = any(keyword in text for keyword in ["api", "http", "https", "request", "fetch", "endpoint", "json"])
    if wants_api and HTTP_REQUEST_TOOL_NAME in available_names:
        if HTTP_REQUEST_TOOL_NAME not in candidates:
            candidates.append(HTTP_REQUEST_TOOL_NAME)

    if candidates:
        logger.info(
            "search_tools: heuristic fallback matched %d tools from query '%s': %s",
            len(candidates),
            query[:80],
            ", ".join(candidates[:5]) + ("..." if len(candidates) > 5 else ""),
        )

    return candidates


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

    available_names = {
        _tool_attr(tool, "full_name") or _tool_attr(tool, "name")
        for tool in tools
    }

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
        "You select tools for research tasks. Be INCLUSIVE - enable all tools that might help.\n\n"
        "## Examples\n\n"
        "**Query:** \"Research Stripe as a company\"\n"
        "**Tools:** `search_engine`, `scrape_as_markdown`, `web_data_linkedin_company_profile`, "
        "`web_data_crunchbase_company`, `web_data_yahoo_finance_business`\n"
        "**External resources:**\n"
        "- Stripe Developer Docs | Official API documentation | https://stripe.com/docs/api\n"
        "- Stripe Status | Service status page | https://status.stripe.com\n\n"
        "**Query:** \"Find info about Elon Musk\"\n"
        "**Tools:** `search_engine`, `scrape_as_markdown`, `web_data_linkedin_person_profile`, "
        "`web_data_x_posts`, `web_data_instagram_profiles`\n"
        "**External resources:**\n"
        "- Wikipedia | Elon Musk biography | https://en.wikipedia.org/wiki/Elon_Musk\n\n"
        "**Query:** \"Analyze sentiment on Nike products\"\n"
        "**Tools:** `search_engine`, `scrape_as_markdown`, `web_data_amazon_product`, "
        "`web_data_amazon_product_reviews`, `web_data_reddit_posts`, `web_data_x_posts`\n"
        "**External resources:**\n"
        "- Nike Investor Relations | Official financial data | https://investors.nike.com\n\n"
        "**Query:** \"Job openings at Google\"\n"
        "**Tools:** `search_engine`, `scrape_as_markdown`, `web_data_linkedin_job_listings`, "
        "`web_data_linkedin_company_profile`\n"
        "**External resources:**\n"
        "- Google Careers | Official job board | https://careers.google.com\n\n"
        "**Query:** \"Bitcoin price and trends\"\n"
        "**Tools:** `search_engine`, `scrape_as_markdown`, `web_data_yahoo_finance_business`, `web_data_reddit_posts`\n"
        "**External resources:**\n"
        "- CoinGecko API | Free crypto prices API | https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd\n"
        "- CoinMarketCap | Crypto market data | https://coinmarketcap.com/currencies/bitcoin/\n\n"
        "## Rules\n"
        "- Skip `search_engine` if query mentions a source with a known public API\n"
        "- `scrape_as_markdown` only when you expect page scraping\n"
        "- external_resources: include direct API endpoints when you know them\n"
        "- Format: Name | Brief description | Full URL"
    )
    user_prompt = (
        f"Query: {query}\n\n"
        "Available tools:\n"
        + "\n".join(tool_lines)
        + "\n\nCall enable_tools with the matching tool names. Do not reply with text."
    )

    try:
        failover_configs = get_llm_config_with_failover(
            agent=agent,
            routing_profile=get_current_eval_routing_profile(),
        )
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
                max_items = get_enabled_tool_limit(agent)

                enable_tools_def = {
                    "type": "function",
                    "function": {
                        "name": "enable_tools",
                        "description": (
                            "Enable tools and optionally suggest external resources. "
                            "Use exact full names from the catalog."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "tool_names": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                    "maxItems": max_items,
                                    "description": "List of full tool names to enable",
                                },
                                "external_resources": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string", "description": "Resource name"},
                                            "description": {"type": "string", "description": "Brief description"},
                                            "url": {"type": "string", "description": "Full URL"},
                                        },
                                        "required": ["name", "description", "url"],
                                    },
                                    "maxItems": 5,
                                    "description": "Public APIs, websites, or datasets with verified URLs",
                                },
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

                # Only force tool_choice if provider supports it (via hint in params)
                tool_choice_hint = params.get("supports_tool_choice")
                tool_choice_supported = tool_choice_hint is None or tool_choice_hint
                if tool_choice_supported:
                    run_kwargs["tool_choice"] = {"type": "function", "function": {"name": "enable_tools"}}

                response = run_completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    params=params,
                    tools=[enable_tools_def],
                    drop_params=True,
                    **run_kwargs,
                )

                token_usage, usage = log_agent_completion(
                    agent,
                    completion_type=PersistentAgentCompletion.CompletionType.TOOL_SEARCH,
                    response=response,
                )
                set_usage_span_attributes(trace.get_current_span(), usage)

                message = response.choices[0].message
                content_text = getattr(message, "content", None) or ""

                requested: List[str] = []
                external_resources: List[Dict[str, str]] = []
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
                        # Extract external resources
                        resources = arguments.get("external_resources") or []
                        if isinstance(resources, list):
                            for res in resources:
                                if isinstance(res, dict) and res.get("name") and res.get("url"):
                                    # Validate URL looks real (starts with http)
                                    url = res.get("url", "")
                                    if url.startswith("http://") or url.startswith("https://"):
                                        external_resources.append({
                                            "name": str(res.get("name", ""))[:100],
                                            "description": str(res.get("description", ""))[:200],
                                            "url": url[:500],
                                        })
                    except Exception:  # pragma: no cover - defensive parsing
                        logger.exception("search_tools.%s: failed to parse tool call; skipping", provider_name)

                enabled_result = None
                if requested:
                    try:
                        enabled_result = enable_callback(agent, requested)
                    except Exception as err:  # pragma: no cover - defensive enabling
                        logger.error("search_tools.%s: enable_tools failed: %s", provider_name, err)
                else:
                    # Inner LLM didn't call enable_tools - log for debugging
                    logger.info(
                        "search_tools.%s: inner LLM did not call enable_tools for query '%s'; "
                        "LLM response: %s",
                        provider_name,
                        query[:80] if query else "",
                        (content_text[:200] + "...") if content_text and len(content_text) > 200 else content_text,
                    )

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

                # Fallback: if the LLM did not call enable_tools, heuristically enable core built-ins
                if not requested:
                    fallback = _fallback_builtin_selection(query or "", content_text or "", available_names)
                    if fallback:
                        try:
                            enabled_result = enable_callback(agent, fallback)
                            logger.info(
                                "search_tools.%s: heuristically enabled tools (no tool call): %s",
                                provider_name,
                                ", ".join(fallback),
                            )
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
                        except Exception as err:  # pragma: no cover - defensive enabling
                            logger.error("search_tools.%s: fallback enable_tools failed: %s", provider_name, err)

                # Build explicit message about what happened
                tools_were_enabled = enabled_result and enabled_result.get("status") == "success" and (
                    enabled_result.get("enabled") or enabled_result.get("already_enabled")
                )

                if not message_lines and not tools_were_enabled:
                    # Make it explicit when no tools were enabled
                    message_lines.append(
                        "No matching tools found for your query. "
                        "Try a more specific query like 'linkedin profile' or 'crunchbase company', "
                        "or use search_engine/scrape_as_markdown for general web research."
                    )

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
                # Include external resources if any were suggested
                if external_resources:
                    response_payload["external_resources"] = external_resources
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
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. Set false to STOP when: all kanban cards are done AND you've sent your final report. Set true if you'll use these tools for more work. Omitting this wastes credits.",
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

    will_continue_work_raw = params.get("will_continue_work", None)
    if will_continue_work_raw is None:
        will_continue_work = None
    elif isinstance(will_continue_work_raw, bool):
        will_continue_work = will_continue_work_raw
    elif isinstance(will_continue_work_raw, str):
        will_continue_work = will_continue_work_raw.lower() == "true"
    else:
        will_continue_work = None

    span.set_attribute("search.query", query)
    logger.info("Agent %s searching for tools: %s", agent.id, query)

    result = search_tools(agent, query)
    if isinstance(result, dict) and result.get("status") == "success" and will_continue_work is False:
        result["auto_sleep_ok"] = True
    return result
