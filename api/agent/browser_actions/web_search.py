"""
Custom browser use agent action for web search using Bright Data MCP search.
"""

import json
import logging
from typing import Any, List, Optional

from opentelemetry import trace
from browser_use import ActionResult

from ..core.web_search_formatter import format_search_results, format_search_error
from ..tools.mcp_manager import execute_mcp_tool
from ..tools.tool_manager import ensure_default_tools_enabled
from ...models import PersistentAgent

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


class _SearchResult:
    """Lightweight struct for formatting search results."""

    def __init__(
        self,
        title: str = "",
        url: str = "",
        text: str = "",
        published_date: Optional[str] = None,
    ) -> None:
        self.title = title
        self.url = url
        self.text = text
        self.published_date = published_date


def _format_brightdata_results(raw_result: Any, query: str) -> str:
    """Normalize Bright Data search payloads into our shared formatter."""
    payload = raw_result
    if isinstance(raw_result, str):
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError:
            return raw_result

    items: Optional[List[Any]] = None
    if isinstance(payload, dict):
        candidate = payload.get("organic") or payload.get("results")
        if isinstance(candidate, list):
            items = candidate
    elif isinstance(payload, list):
        items = payload

    if not items:
        return format_search_error("No search results returned from Bright Data", query)

    normalized: List[_SearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name") or ""
        url = item.get("url") or item.get("link") or ""
        text = (
            item.get("snippet")
            or item.get("description")
            or item.get("text")
            or item.get("content")
            or ""
        )
        published = (
            item.get("published_date")
            or item.get("publishedAt")
            or item.get("date")
        )
        normalized.append(_SearchResult(title=title, url=url, text=text, published_date=published))

    if not normalized:
        return format_search_error("No search results returned from Bright Data", query)

    return format_search_results(normalized, query)


def _resolve_persistent_agent(
    *,
    persistent_agent_id: Optional[str] = None,
    browser_agent_id: Optional[str] = None,
    user_id: Optional[int] = None,
    organization_id: Optional[str] = None,
) -> Optional[PersistentAgent]:
    """Resolve a persistent agent from any available identifiers."""
    if persistent_agent_id:
        try:
            return PersistentAgent.objects.select_related("user", "organization").get(
                id=persistent_agent_id
            )
        except PersistentAgent.DoesNotExist:
            logger.warning("Persistent agent %s not found for browser search action", persistent_agent_id)
        except Exception:
            logger.exception("Failed to load persistent agent %s", persistent_agent_id)

    if browser_agent_id:
        try:
            from ...models import BrowserUseAgent  # Lazy import to avoid circulars

            browser_agent = BrowserUseAgent.objects.select_related("persistent_agent").get(
                id=browser_agent_id
            )
            persistent_agent = getattr(browser_agent, "persistent_agent", None)
            if persistent_agent:
                return persistent_agent
        except BrowserUseAgent.DoesNotExist:
            logger.warning("BrowserUseAgent %s not found while resolving persistent agent", browser_agent_id)
        except Exception:
            logger.exception("Failed to resolve persistent agent from browser agent %s", browser_agent_id)

    if organization_id:
        fallback = (
            PersistentAgent.objects.filter(organization_id=organization_id, is_active=True)
            .order_by("created_at")
            .first()
        )
        if fallback:
            return fallback

    if user_id:
        fallback = (
            PersistentAgent.objects.filter(user_id=user_id, is_active=True)
            .order_by("created_at")
            .first()
        )
        if fallback:
            return fallback

    return None


def register_web_search_action(
    controller,
    persistent_agent_id: Optional[str] = None,
    browser_agent_id: Optional[str] = None,
    user_id: Optional[int] = None,
    organization_id: Optional[str] = None,
):
    """Register the Bright Data search action with the given controller."""

    resolved_agent: Optional[PersistentAgent] = _resolve_persistent_agent(
        persistent_agent_id=persistent_agent_id,
        browser_agent_id=browser_agent_id,
        user_id=user_id,
        organization_id=organization_id,
    )
    logger.info(
        "Registering mcp_brightdata_search_engine action to controller %s (agent=%s)",
        controller,
        getattr(resolved_agent, "id", persistent_agent_id),
    )

    @controller.action(
        "Search the web using Bright Data search engine. Returns relevant web content for the query."
    )
    def mcp_brightdata_search_engine(query: str) -> ActionResult:
        """
        Search the web using Bright Data MCP search.

        Args:
            query: Search query string. Be specific and detailed for best results.

        Returns:
            ActionResult containing search results with titles, URLs, and content excerpts.
        """
        with tracer.start_as_current_span("Browser Agent Web Search") as span:
            span.set_attribute("search.query", query)

            if not query:
                logger.warning("Empty search query provided")
                return ActionResult(
                    extracted_content="Error: Search query cannot be empty",
                    include_in_memory=False,
                )

            agent_ctx = resolved_agent
            if agent_ctx is None:
                span.add_event("Bright Data search unavailable - no agent context")
                logger.error("Bright Data search unavailable: no persistent agent could be resolved")
                return ActionResult(
                    extracted_content=format_search_error(
                        "Web search is not available because no eligible agent context could be found.", query
                    ),
                    include_in_memory=False,
                )

            try:
                ensure_default_tools_enabled(agent_ctx, allowed_server_names=("brightdata",))
            except Exception:
                logger.exception("Failed to ensure default MCP tools for agent %s", agent_ctx.id)

            try:
                span.set_attribute("search.engine", "brightdata_mcp")
                response = execute_mcp_tool(
                    agent_ctx,
                    "mcp_brightdata_search_engine",
                    {"query": query},
                )
            except Exception as exc:
                logger.exception("Bright Data MCP search failed for agent %s", agent_ctx.id)
                response = {"status": "error", "message": str(exc)}

            status = response.get("status") if isinstance(response, dict) else None
            if status == "success":
                result_text = _format_brightdata_results(response.get("result"), query)
                return ActionResult(extracted_content=result_text, include_in_memory=True)

            message = None
            if isinstance(response, dict):
                message = response.get("message") or response.get("result")

            if message:
                span.add_event("Bright Data search error")
                span.set_attribute("error.message", str(message))
                logger.warning(
                    "Bright Data search failed for agent %s: %s",
                    getattr(agent_ctx, "id", None),
                    message,
                )

            return ActionResult(
                extracted_content=format_search_error(
                    message or "Web search failed for an unknown reason.", query
                ),
                include_in_memory=False,
            )
