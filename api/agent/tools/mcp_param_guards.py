import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from django.db import DatabaseError

from ...services.tool_settings import get_tool_settings_for_owner

logger = logging.getLogger(__name__)


class MCPToolParamGuard:
    """Base guard for validating MCP tool parameters before execution."""

    server_name: Optional[str] = None
    tool_name: Optional[str] = None

    def matches(self, server_name: str, tool_name: str) -> bool:
        server_match = self.server_name is None or self.server_name == server_name
        tool_match = self.tool_name is None or self.tool_name == tool_name
        return server_match and tool_match

    def validate(self, params: Dict[str, Any], owner: Any) -> Optional[Dict[str, str]]:
        """Return an error payload if params should be rejected."""
        return None


def _extract_candidate_urls(params: Dict[str, Any]) -> List[str]:
    if not isinstance(params, dict):
        return []
    urls: List[str] = []
    string_keys = {"url", "link", "page", "target_url"}
    list_keys = {"urls", "links", "pages", "targets", "target_urls"}
    for key, value in params.items():
        if key in string_keys and isinstance(value, str):
            urls.append(value)
        elif key in list_keys and isinstance(value, list):
            urls.extend([v for v in value if isinstance(v, str)])
    return urls


def _is_pdf_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.path.lower().endswith(".pdf")


class BrightDataPdfGuard(MCPToolParamGuard):
    """Reject Bright Data scrape calls for PDF URLs."""

    server_name = "brightdata"

    def matches(self, server_name: str, tool_name: str) -> bool:
        if not super().matches(server_name, tool_name):
            return False
        return tool_name in {"scrape_as_markdown", "scrape_as_html"}

    def validate(self, params: Dict[str, Any], owner: Any) -> Optional[Dict[str, str]]:
        urls = _extract_candidate_urls(params)
        if any(_is_pdf_url(u) for u in urls):
            return {
                "status": "error",
                "message": "PDF scraping is not supported for Bright Data snapshots. Use spawn_web_task to read PDFs instead.",
            }
        return None


class BrightDataSearchEngineBatchGuard(MCPToolParamGuard):
    """Enforce per-plan query limits for Bright Data batch search."""

    server_name = "brightdata"
    tool_name = "search_engine_batch"

    def validate(self, params: Dict[str, Any], owner: Any) -> Optional[Dict[str, str]]:
        queries = params.get("queries")
        if not isinstance(queries, list):
            return None
        try:
            settings = get_tool_settings_for_owner(owner)
        except DatabaseError:
            logger.error("Failed to load tool settings for search_engine_batch limit.", exc_info=True)
            return None
        limit = getattr(settings, "search_engine_batch_query_limit", None)
        if not isinstance(limit, int) or limit <= 0:
            return None
        if len(queries) > limit:
            return {
                "status": "error",
                "message": (
                    f"Maximum number of queries ({limit}) exceeded for search_engine_batch; "
                    f"received {len(queries)}."
                ),
            }
        return None


class MCPParamGuardRegistry:
    """Registry of parameter guards keyed by provider/tool."""

    def __init__(self, guards: Optional[List[MCPToolParamGuard]] = None):
        self._guards = list(guards or [])

    @classmethod
    def default(cls) -> "MCPParamGuardRegistry":
        return cls(
            guards=[
                BrightDataPdfGuard(),
                BrightDataSearchEngineBatchGuard(),
            ]
        )

    def validate(
        self,
        server_name: str,
        tool_name: str,
        params: Dict[str, Any],
        owner: Any,
    ) -> Optional[Dict[str, str]]:
        for guard in self._guards:
            if guard.matches(server_name, tool_name):
                try:
                    error = guard.validate(params, owner)
                except Exception:
                    logger.exception(
                        "Failed to validate MCP params with %s for %s/%s",
                        guard.__class__.__name__,
                        server_name,
                        tool_name,
                    )
                    return {
                        "status": "error",
                        "message": "Tool parameters could not be validated.",
                    }
                if error:
                    return error
        return None
