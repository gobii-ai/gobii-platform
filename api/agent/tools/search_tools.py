"""
Tool search orchestration for persistent agents.

Provides a unified `search_tools` function that queries the LLM once across
both MCP-discovered tools and builtin tools, then enables any selected tools.
"""

import json
import logging
import re
from functools import partial
from typing import Any, Callable, Dict, Iterable, List, Optional

import litellm  # re-exported for tests that patch LiteLLM directly
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from litellm import drop_params
from opentelemetry import trace

from ...models import MCPServerConfig, PersistentAgent, PersistentAgentCompletion
from ...services.pipedream_apps import (
    PipedreamCatalogError,
    PipedreamCatalogService,
    enable_pipedream_apps_for_agent,
    get_effective_pipedream_app_slugs_for_agent,
)
from ...services.tool_settings import get_tool_settings_for_owner
from ...evals.execution import get_current_eval_routing_profile
from ..core.llm_config import LLMNotConfiguredError, get_llm_config_with_failover
from ..core.llm_utils import run_completion
from ..core.token_usage import log_agent_completion, set_usage_span_attributes
from .global_skills import enable_global_skills, get_compatible_global_skills
from .mcp_manager import get_mcp_manager
from .skill_utils import normalize_skill_tool_ids
from .tool_manager import (
    enable_tools,
    CREATE_IMAGE_TOOL_NAME,
    HTTP_REQUEST_TOOL_NAME,
    get_available_builtin_tool_entries,
    get_available_custom_tool_entries,
    get_enabled_tool_limit,
)
from .autotool_heuristics import find_matching_tools

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

ToolSearchResult = Dict[str, Any]


def _has_active_pipedream_runtime() -> bool:
    return MCPServerConfig.objects.filter(
        scope=MCPServerConfig.Scope.PLATFORM,
        name="pipedream",
        is_active=True,
    ).exists()


def _build_console_url(route_name: str) -> str:
    try:
        path = reverse(route_name)
    except NoReverseMatch:
        logger.debug("search_tools: failed to reverse route %s", route_name, exc_info=True)
        return ""

    base_url = (getattr(settings, "PUBLIC_SITE_URL", "") or "").strip().rstrip("/")
    if base_url:
        return f"{base_url}{path}"
    return path


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

    wants_image = any(
        keyword in text
        for keyword in [
            "generate image",
            "generate an image",
            "image generation",
            "create image",
            "make image",
            "render image",
            "edit image",
            "modify image",
            "transform image",
            "image to image",
            "style transfer",
            "illustration",
            "illustrate",
            "create logo",
            "logo design",
            "poster design",
            "thumbnail design",
            "concept art",
            "artwork",
        ]
    )
    if wants_image and CREATE_IMAGE_TOOL_NAME in available_names:
        if CREATE_IMAGE_TOOL_NAME not in candidates:
            candidates.append(CREATE_IMAGE_TOOL_NAME)

    if candidates:
        logger.info(
            "search_tools: heuristic fallback matched %d tools from query '%s': %s",
            len(candidates),
            query[:80],
            ", ".join(candidates[:5]) + ("..." if len(candidates) > 5 else ""),
        )

    return candidates


def _find_tool_by_suffix(
    available_names: Iterable[str],
    suffix: str,
    *,
    exclude_suffixes: Optional[Iterable[str]] = None,
) -> Optional[str]:
    exclude_suffixes = set(exclude_suffixes or [])
    candidates = []
    for name in available_names:
        if name == suffix or name.endswith(f"_{suffix}"):
            if any(name.endswith(f"_{exclude}") or name == exclude for exclude in exclude_suffixes):
                continue
            candidates.append(name)
    if not candidates:
        return None
    return sorted(candidates, key=lambda v: (len(v), v))[0]


def _build_tool_examples(available_names: set[str]) -> str:
    def example(query_text: str, suffixes: List[str]) -> Optional[str]:
        tools: List[str] = []
        for suffix in suffixes:
            tool_name = _find_tool_by_suffix(
                available_names,
                suffix,
                exclude_suffixes=["search_engine_batch"] if suffix == "search_engine" else None,
            )
            if tool_name:
                tools.append(tool_name)
        if len(tools) < 2:
            return None
        tool_list = ", ".join(f"`{name}`" for name in tools)
        return f"**Query:** \"{query_text}\"\n**Tools:** {tool_list}\n"

    examples = [
        example(
            "Research Stripe as a company",
            [
                "search_engine",
                "scrape_as_markdown",
                "web_data_linkedin_company_profile",
                "web_data_crunchbase_company",
                "web_data_yahoo_finance_business",
            ],
        ),
        example(
            "Find info about Elon Musk",
            [
                "search_engine",
                "scrape_as_markdown",
                "web_data_linkedin_person_profile",
                "web_data_x_posts",
                "web_data_instagram_profiles",
            ],
        ),
        example(
            "Analyze sentiment on Nike products",
            [
                "search_engine",
                "scrape_as_markdown",
                "web_data_amazon_product",
                "web_data_amazon_product_reviews",
                "web_data_reddit_posts",
                "web_data_x_posts",
            ],
        ),
        example(
            "Job openings at Google",
            [
                "search_engine",
                "scrape_as_markdown",
                "web_data_linkedin_job_listings",
                "web_data_linkedin_company_profile",
            ],
        ),
        example(
            "Bitcoin price and trends",
            [
                "search_engine",
                "scrape_as_markdown",
                "web_data_yahoo_finance_business",
                "web_data_reddit_posts",
            ],
        ),
        example(
            "GitHub repository file details",
            [
                "web_data_github_repository_file",
                "search_engine",
            ],
        ),
    ]
    example_text = "\n".join(item.strip() for item in examples if item)
    return example_text


def _build_app_lines(
    app_catalog: Iterable[Any],
    *,
    enabled_app_slugs: Optional[Iterable[str]] = None,
) -> list[str]:
    enabled_set = set(enabled_app_slugs or [])
    lines: list[str] = []
    for app in app_catalog:
        slug = _tool_attr(app, "slug")
        name = _tool_attr(app, "name")
        if not isinstance(slug, str) or not slug:
            continue
        label = name if isinstance(name, str) and name.strip() else slug
        status = "enabled" if slug in enabled_set else "not enabled"
        lines.append(f"- {slug} | {label} [{status}]")
    return lines


def _build_global_skill_lines(global_skills: Iterable[Any]) -> list[str]:
    lines: list[str] = []
    for skill in global_skills:
        name = _tool_attr(skill, "name")
        if not isinstance(name, str) or not name:
            continue
        description = _strip_description(_tool_attr(skill, "description", "") or "")
        normalized_tools = list(normalize_skill_tool_ids(_tool_attr(skill, "tools", []) or []))
        tool_text = ", ".join(normalized_tools) if normalized_tools else "(none)"
        line = f"- {name}"
        if description:
            line += f": {description}"
        line += f" | tools: {tool_text}"
        lines.append(line)
    return lines


def _build_search_tool_definitions(
    *,
    max_items: int,
    include_global_skills: bool,
    include_app_enablement: bool,
) -> list[dict[str, Any]]:
    tool_defs: list[dict[str, Any]] = [
        {
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
    ]
    if include_global_skills:
        tool_defs.append(
            {
                "type": "function",
                "function": {
                    "name": "enable_global_skills",
                    "description": (
                        "Import global skills into the agent's local skill history. "
                        "Use exact names from the Available global skills list."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                                "maxItems": 20,
                                "description": "List of exact global skill names to enable",
                            },
                        },
                        "required": ["skill_names"],
                    },
                },
            }
        )
    if include_app_enablement:
        tool_defs.append(
            {
                "type": "function",
                "function": {
                    "name": "enable_apps",
                    "description": (
                        "Enable Pipedream apps for future tool discovery. "
                        "Use exact app slugs from the Available Pipedream apps list."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "app_slugs": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                                "maxItems": 20,
                                "description": "List of exact Pipedream app slugs to enable",
                            },
                        },
                        "required": ["app_slugs"],
                    },
                },
            }
        )
    return tool_defs


def _parse_search_tool_calls(tool_calls: Iterable[Any], *, provider_name: str) -> dict[str, list[Any]]:
    requested_tools: list[str] = []
    requested_skills: list[str] = []
    requested_apps: list[str] = []
    external_resources: list[dict[str, str]] = []

    for tool_call in tool_calls:
        try:
            if not tool_call:
                continue
            function_block = getattr(tool_call, "function", None) or tool_call.get("function")
            if not function_block:
                continue
            function_name = getattr(function_block, "name", None) or function_block.get("name")
            raw_args = getattr(function_block, "arguments", None) or function_block.get("arguments") or "{}"
            arguments = json.loads(raw_args)
            if function_name == "enable_tools":
                names = arguments.get("tool_names") or []
                if isinstance(names, list):
                    for name in names:
                        if isinstance(name, str) and name not in requested_tools:
                            requested_tools.append(name)
                resources = arguments.get("external_resources") or []
                if isinstance(resources, list):
                    for res in resources:
                        if isinstance(res, dict) and res.get("name") and res.get("url"):
                            url = res.get("url", "")
                            if url.startswith("http://") or url.startswith("https://"):
                                external_resources.append(
                                    {
                                        "name": str(res.get("name", ""))[:100],
                                        "description": str(res.get("description", ""))[:200],
                                        "url": url[:500],
                                    }
                                )
            elif function_name == "enable_global_skills":
                skill_names = arguments.get("skill_names") or []
                if isinstance(skill_names, list):
                    for skill_name in skill_names:
                        if isinstance(skill_name, str) and skill_name not in requested_skills:
                            requested_skills.append(skill_name)
            elif function_name == "enable_apps":
                app_slugs = arguments.get("app_slugs") or []
                if isinstance(app_slugs, list):
                    for app_slug in app_slugs:
                        if isinstance(app_slug, str) and app_slug not in requested_apps:
                            requested_apps.append(app_slug)
        except Exception:  # pragma: no cover - defensive parsing
            logger.exception("search_tools.%s: failed to parse tool call; skipping", provider_name)

    return {
        "tools": requested_tools,
        "skills": requested_skills,
        "apps": requested_apps,
        "external_resources": external_resources,
    }


def _normalize_requested_selection(
    requested: list[str],
    available_names: set[str],
    *,
    provider_name: str,
    item_label: str,
) -> list[str]:
    valid_requested = [name for name in requested if name in available_names]
    invalid_requested = [name for name in requested if name not in available_names]
    if invalid_requested:
        logger.info(
            "search_tools.%s: ignoring invalid %s: %s",
            provider_name,
            item_label,
            ", ".join(invalid_requested[:10]) + ("..." if len(invalid_requested) > 10 else ""),
        )
    return valid_requested


def _section_payload(
    *,
    enabled: Optional[list[str]] = None,
    already_enabled: Optional[list[str]] = None,
    evicted: Optional[list[str]] = None,
    invalid: Optional[list[str]] = None,
    conflicts: Optional[list[str]] = None,
    effective_apps: Optional[list[str]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "enabled": list(enabled or []),
        "already_enabled": list(already_enabled or []),
    }
    if evicted is not None:
        payload["evicted"] = list(evicted)
    if invalid is not None:
        payload["invalid"] = list(invalid)
    if conflicts is not None:
        payload["conflicts"] = list(conflicts)
    if effective_apps is not None:
        payload["effective_apps"] = list(effective_apps)
    return payload


def _append_section_summary(
    message_lines: list[str],
    result: Optional[dict[str, Any]],
    *,
    enabled_label: str,
    already_enabled_label: str,
    evicted_label: Optional[str] = None,
    invalid_label: Optional[str] = None,
    conflicts_label: Optional[str] = None,
) -> None:
    if not result or result.get("status") != "success":
        return

    summary: list[str] = []
    if result.get("enabled"):
        summary.append(f"{enabled_label}: {', '.join(result['enabled'])}")
    if result.get("already_enabled"):
        summary.append(f"{already_enabled_label}: {', '.join(result['already_enabled'])}")
    if evicted_label and result.get("evicted"):
        summary.append(f"{evicted_label}: {', '.join(result['evicted'])}")
    if invalid_label and result.get("invalid"):
        summary.append(f"{invalid_label}: {', '.join(result['invalid'])}")
    if conflicts_label and result.get("conflicts"):
        summary.append(f"{conflicts_label}: {', '.join(result['conflicts'])}")
    if summary:
        message_lines.append("; ".join(summary))


def _search_with_llm(
    agent: PersistentAgent,
    query: str,
    provider_name: str,
    catalog: Iterable[Any],
    enable_callback: Callable[[PersistentAgent, List[str]], Dict[str, Any]],
    empty_message: str,
    *,
    global_skill_catalog: Optional[Iterable[Any]] = None,
    enable_apps_callback: Optional[Callable[[PersistentAgent, List[str]], Dict[str, Any]]] = None,
    pipedream_app_catalog: Optional[Iterable[Any]] = None,
    enabled_app_slugs: Optional[Iterable[str]] = None,
    auto_enable_apps: bool = True,
) -> ToolSearchResult:
    tools = list(catalog)
    global_skills = list(global_skill_catalog or [])
    app_catalog = list(pipedream_app_catalog or [])
    logger.info(
        "search_tools.%s: %d tools available, %d global skills available, %d pipedream apps available",
        provider_name,
        len(tools),
        len(global_skills),
        len(app_catalog),
    )

    if not tools and not global_skills and not app_catalog:
        return {"status": "success", "tools": [], "message": empty_message}
    available_names = {
        _tool_attr(tool, "full_name") or _tool_attr(tool, "name")
        for tool in tools
    }
    available_global_skill_names = {
        _tool_attr(skill, "name")
        for skill in global_skills
        if isinstance(_tool_attr(skill, "name"), str)
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

    global_skill_lines = _build_global_skill_lines(global_skills)
    app_lines = _build_app_lines(app_catalog, enabled_app_slugs=enabled_app_slugs)
    enable_apps_manually_url = _build_console_url("console-mcp-servers")
    manual_app_guidance = (
        f'If a needed Pipedream app is not enabled yet and you require it, tell the user to go to "Add Apps" here: '
        f"{enable_apps_manually_url} and search for the exact app slug.\n"
        "Do this sparingly and only if you truly require the integration. "
        "You may already have the tools you need for integration via http_request or other methods."
    )

    examples_text = _build_tool_examples(available_names)
    examples_block = f"## Examples\n\n{examples_text}\n\n" if examples_text else ""
    image_generation_rules = ""
    if CREATE_IMAGE_TOOL_NAME in available_names:
        image_generation_rules = (
            f"- If the user asks to generate or design a NEW image asset, include `{CREATE_IMAGE_TOOL_NAME}`.\n"
            f"- If the user asks to edit, transform, restyle, or preserve details from an existing image, include `{CREATE_IMAGE_TOOL_NAME}`.\n"
            f"- For edit/transform requests, plan to pass `source_images` so the model can preserve identity, logos, text, or layout.\n"
            f"- Do not include `{CREATE_IMAGE_TOOL_NAME}` for image analysis, OCR, or extracting information from existing images.\n"
        )

    system_prompt = (
        "You select tools for research tasks. Be INCLUSIVE - enable all tools that might help.\n"
        "CRITICAL: Use EXACT tool names, skill names, and app slugs from the lists below. Never invent or modify names.\n"
        "If nothing matches, do NOT call any tool.\n\n"
        f"{examples_block}"
        "## Format\n"
        "Call enable_tools with tool_names copied verbatim from the Available tools list.\n"
        "## Rules\n"
        "- Only include tools that appear in Available tools.\n"
        f"{image_generation_rules}"
        "- external_resources: include direct API endpoints when you know them\n"
        "- Format: Name | Brief description | Full URL"
    )
    if global_skill_lines:
        system_prompt += (
            "\n- Only include skill names that appear in Available global skills.\n"
            "Call enable_global_skills with skill_names copied verbatim from the Available global skills list.\n"
        )
    if app_lines:
        if auto_enable_apps and enable_apps_callback is not None:
            system_prompt += (
                "\n- Only include app slugs that appear in Available Pipedream apps.\n"
                "Call enable_apps with app_slugs copied verbatim from the Available Pipedream apps list.\n"
                "Do not call enable_tools or enable_global_skills in the same response as enable_apps.\n"
                "If a needed Pipedream app is not enabled yet, call enable_apps with exact app slugs and stop there.\n"
                "Do this sparingly and only if you truly require the integration. "
                "You may already have the tools you need for integration via http_request or other methods."
                "Example (placeholders, do not copy names):\n"
                "tool_names: [\"<TOOL_NAME_FROM_LIST>\", \"<ANOTHER_TOOL_NAME_FROM_LIST>\"]\n"
                "skill_names: [\"<SKILL_NAME_FROM_LIST>\"]\n"
                "app_slugs: [\"<APP_SLUG_FROM_LIST>\"]\n"
            )
        else:
            system_prompt += (
                "\n- Do not call enable_apps. Automatic Pipedream app enablement is disabled.\n"
                f"{manual_app_guidance}"
                "Only enable tools that are already available in Available tools.\n"
                "Example (placeholders, do not copy names):\n"
                "tool_names: [\"<TOOL_NAME_FROM_LIST>\", \"<ANOTHER_TOOL_NAME_FROM_LIST>\"]\n"
                "skill_names: [\"<SKILL_NAME_FROM_LIST>\"]\n"
            )
    else:
        system_prompt += (
            "\nExample (placeholders, do not copy names):\n"
            "tool_names: [\"<TOOL_NAME_FROM_LIST>\", \"<ANOTHER_TOOL_NAME_FROM_LIST>\"]\n"
            "skill_names: [\"<SKILL_NAME_FROM_LIST>\"]\n"
        )
    user_prompt = f"Query: {query}\n\nAvailable tools:\n" + "\n".join(tool_lines)
    if global_skill_lines:
        user_prompt += "\n\nAvailable global skills:\n" + "\n".join(global_skill_lines)
    if app_lines:
        user_prompt += (
            "\n\nAvailable Pipedream apps:\n"
            + "\n".join(app_lines)
            + "\n\nUse ONLY tool names, skill names, and app slugs from the lists above."
        )
        if auto_enable_apps and enable_apps_callback is not None:
            user_prompt += " If none match, do not call enable_tools, enable_global_skills, or enable_apps."
        else:
            user_prompt += (
                f' If a needed app is not enabled, you may tell the user to go to "Add Apps" '
                f"here: {enable_apps_manually_url} and search for the exact app slug. "
                "Do this sparingly and only if you truly require the integration. "
                "You may already have the tools you need for integration via http_request or other methods."
            )
            user_prompt += " If none match, do not call enable_tools or enable_global_skills."
    else:
        user_prompt += "\n\nUse ONLY tool names and skill names from the lists above."
        user_prompt += " If none match, do not call enable_tools or enable_global_skills."

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

                tool_defs = _build_search_tool_definitions(
                    max_items=max_items,
                    include_global_skills=bool(global_skill_lines),
                    include_app_enablement=bool(app_lines and auto_enable_apps and enable_apps_callback is not None),
                )

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
                    tools=tool_defs,
                    drop_params=True,
                    **run_kwargs,
                )

                token_usage, usage = log_agent_completion(
                    agent,
                    completion_type=PersistentAgentCompletion.CompletionType.TOOL_SEARCH,
                    response=response,
                    model=model,
                    provider=provider,
                )
                set_usage_span_attributes(trace.get_current_span(), usage)

                message = response.choices[0].message
                content_text = getattr(message, "content", None) or ""

                parsed_calls = _parse_search_tool_calls(
                    getattr(message, "tool_calls", None) or [],
                    provider_name=provider_name,
                )
                requested = _normalize_requested_selection(
                    parsed_calls["tools"],
                    available_names,
                    provider_name=provider_name,
                    item_label="tool names",
                )
                requested_skills = _normalize_requested_selection(
                    parsed_calls["skills"],
                    available_global_skill_names,
                    provider_name=provider_name,
                    item_label="global skill names",
                )
                requested_apps = parsed_calls["apps"]
                external_resources = parsed_calls["external_resources"]
                enabled_apps_result = None
                if requested_apps and enable_apps_callback is not None:
                    try:
                        enabled_apps_result = enable_apps_callback(agent, requested_apps)
                    except Exception as err:  # pragma: no cover - defensive enabling
                        logger.error("search_tools.%s: enable_apps failed: %s", provider_name, err)

                if enabled_apps_result and enabled_apps_result.get("status") == "success":
                    message_lines: List[str] = []
                    if content_text:
                        message_lines.append(content_text.strip())
                    summary: List[str] = []
                    if enabled_apps_result.get("enabled"):
                        summary.append(f"Enabled apps: {', '.join(enabled_apps_result['enabled'])}")
                    if enabled_apps_result.get("already_enabled"):
                        summary.append(f"Already enabled apps: {', '.join(enabled_apps_result['already_enabled'])}")
                    if enabled_apps_result.get("invalid"):
                        summary.append(f"Invalid apps: {', '.join(enabled_apps_result['invalid'])}")
                    if summary:
                        message_lines.append("; ".join(summary))
                    if enabled_apps_result.get("enabled") or enabled_apps_result.get("already_enabled"):
                        message_lines.append(
                            "Pipedream apps are ready. Run search_tools again to discover and enable the specific tools for those apps."
                        )
                    else:
                        message_lines.append("No Pipedream apps were enabled. Search again with one of the listed app slugs.")
                    response_payload: ToolSearchResult = {
                        "status": "success",
                        "message": "\n".join([line for line in message_lines if line]) or "",
                        "apps": _section_payload(
                            enabled=enabled_apps_result.get("enabled", []),
                            already_enabled=enabled_apps_result.get("already_enabled", []),
                            invalid=enabled_apps_result.get("invalid", []),
                            effective_apps=enabled_apps_result.get("effective_apps", []),
                        ),
                    }
                    return response_payload

                enabled_global_skills_result = None
                if requested_skills:
                    try:
                        enabled_global_skills_result = enable_global_skills(
                            agent,
                            requested_skills,
                            available_skills=global_skills,
                        )
                    except Exception as err:  # pragma: no cover - defensive enabling
                        logger.error("search_tools.%s: enable_global_skills failed: %s", provider_name, err)

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
                _append_section_summary(
                    message_lines,
                    enabled_global_skills_result,
                    enabled_label="Enabled skills",
                    already_enabled_label="Already enabled skills",
                    conflicts_label="Skill conflicts",
                )
                _append_section_summary(
                    message_lines,
                    enabled_result,
                    enabled_label="Enabled tools",
                    already_enabled_label="Already enabled tools",
                    evicted_label="Evicted (LRU)",
                    invalid_label="Invalid tools",
                )

                # Fallback only when the model made no explicit tool or skill selection.
                if not requested and not requested_skills:
                    fallback = _fallback_builtin_selection(query or "", content_text or "", available_names)
                    fallback = [name for name in fallback if name in available_names]
                    if fallback:
                        try:
                            enabled_result = enable_callback(agent, fallback)
                            logger.info(
                                "search_tools.%s: heuristically enabled tools (no tool call): %s",
                                provider_name,
                                ", ".join(fallback),
                            )
                            _append_section_summary(
                                message_lines,
                                enabled_result,
                                enabled_label="Enabled tools",
                                already_enabled_label="Already enabled tools",
                                evicted_label="Evicted (LRU)",
                                invalid_label="Invalid tools",
                            )
                        except Exception as err:  # pragma: no cover - defensive enabling
                            logger.error("search_tools.%s: fallback enable_tools failed: %s", provider_name, err)

                # Build explicit message about what happened
                skills_were_enabled = (
                    enabled_global_skills_result
                    and enabled_global_skills_result.get("status") == "success"
                    and (
                        enabled_global_skills_result.get("enabled")
                        or enabled_global_skills_result.get("already_enabled")
                    )
                )
                tools_were_enabled = enabled_result and enabled_result.get("status") == "success" and (
                    enabled_result.get("enabled") or enabled_result.get("already_enabled")
                )

                if not message_lines and not tools_were_enabled and not skills_were_enabled:
                    # Make it explicit when no tools were enabled
                    message_lines.append(
                        "No matching tools or global skills found for your query. "
                        "Try a more specific query like 'linkedin profile' or 'crunchbase company', "
                        "or use the web search/scrape tools from the available list."
                    )

                response_payload: ToolSearchResult = {
                    "status": "success",
                    "message": "\n".join([line for line in message_lines if line]) or "",
                }
                if enabled_global_skills_result and enabled_global_skills_result.get("status") == "success":
                    response_payload["skills"] = _section_payload(
                        enabled=enabled_global_skills_result.get("enabled", []),
                        already_enabled=enabled_global_skills_result.get("already_enabled", []),
                        conflicts=enabled_global_skills_result.get("conflicts", []),
                    )
                if enabled_result and enabled_result.get("status") == "success":
                    response_payload["tools"] = _section_payload(
                        enabled=enabled_result.get("enabled", []),
                        already_enabled=enabled_result.get("already_enabled", []),
                        evicted=enabled_result.get("evicted", []),
                        invalid=enabled_result.get("invalid", []),
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

    builtin_catalog: List[Dict[str, Any]] = [
        {
            "full_name": entry.full_name,
            "description": entry.description,
            "parameters": entry.parameters,
        }
        for entry in get_available_builtin_tool_entries(agent).values()
    ]
    custom_catalog: List[Dict[str, Any]] = [
        {
            "full_name": entry.full_name,
            "description": entry.description,
            "parameters": entry.parameters,
        }
        for entry in get_available_custom_tool_entries(agent).values()
    ]
    global_skill_catalog = get_compatible_global_skills(agent)

    combined_catalog: List[Any] = list(mcp_tools) + builtin_catalog + custom_catalog
    pipedream_app_catalog: list[Any] = []
    enabled_app_slugs: list[str] = []
    owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
    auto_enable_apps = True
    if owner is not None:
        auto_enable_apps = get_tool_settings_for_owner(owner).tool_search_auto_enable_apps
    if _has_active_pipedream_runtime():
        try:
            pipedream_app_catalog = PipedreamCatalogService().search_apps(query, limit=20)
            enabled_app_slugs = get_effective_pipedream_app_slugs_for_agent(agent)
        except PipedreamCatalogError as exc:
            logger.warning("search_tools: unable to search Pipedream apps for agent %s: %s", agent.id, exc)

    if not combined_catalog and not global_skill_catalog and not pipedream_app_catalog:
        logger.info("search_tools: no tools available for agent %s", agent.id)
        return {"status": "success", "tools": [], "message": "No tools available"}

    return _search_with_llm(
        agent=agent,
        query=query,
        provider_name="catalog",
        catalog=combined_catalog,
        enable_callback=enable_tools,
        empty_message="No tools available",
        global_skill_catalog=global_skill_catalog,
        enable_apps_callback=(
            partial(
                enable_pipedream_apps_for_agent,
                available_app_slugs=[_tool_attr(app, "slug") for app in pipedream_app_catalog],
            )
            if pipedream_app_catalog and auto_enable_apps
            else None
        ),
        pipedream_app_catalog=pipedream_app_catalog,
        enabled_app_slugs=enabled_app_slugs,
        auto_enable_apps=auto_enable_apps,
    )


def get_search_tools_tool() -> Dict[str, Any]:
    """Return the search_tools tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "search_tools",
            "description": (
                "Search your internal tool and skill catalog to discover and enable tools and skills for a task, including saved custom tools. "
                "NOT for web search - use the web search tool from the catalog (e.g., mcp_brightdata_search_engine). "
                "Call this when tasks change and you need different capabilities."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Description of what you want to accomplish or what kind of tools and skills you're looking for",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done. Omitting this stops you for good—choose wisely.",
                    }
                },
                "required": ["query", "will_continue_work"],
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
