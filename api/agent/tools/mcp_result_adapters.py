import contextlib
import contextvars
import json
import logging
import re
from typing import Any, List, Optional, Tuple

from django.db import DatabaseError

from api.services.tool_settings import get_tool_settings_for_owner

logger = logging.getLogger(__name__)
_RESULT_OWNER_CONTEXT: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "mcp_result_owner",
    default=None,
)
_DATA_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[([^\]]*)\]\(\s*data:image\/[a-z0-9.+-]+;base64,[^)]+?\s*\)",
    re.IGNORECASE,
)


@contextlib.contextmanager
def mcp_result_owner_context(owner: Any):
    """Provide owner context for adapters that need plan-specific settings."""
    token = _RESULT_OWNER_CONTEXT.set(owner)
    try:
        yield
    finally:
        _RESULT_OWNER_CONTEXT.reset(token)


def scrub_markdown_data_images(text: str) -> str:
    return _DATA_IMAGE_MARKDOWN_RE.sub(
        lambda match: f"![{match.group(1)}]()",
        text,
    )


def _strip_keys(
    node: Any,
    *,
    strip_keys: Optional[set[str]] = None,
    strip_key_prefixes: Tuple[str, ...] = (),
    strip_key_suffixes: Tuple[str, ...] = (),
) -> None:
    """Recursively strip keys (and prefix/suffix matches) from nested dict/list/tuple structures."""
    keys = strip_keys or set()
    prefixes = strip_key_prefixes or ()
    suffixes = strip_key_suffixes or ()

    if isinstance(node, dict):
        for key in list(node.keys()):
            if key in keys or key.startswith(prefixes) or key.endswith(suffixes):
                node.pop(key, None)
            else:
                _strip_keys(node[key], strip_keys=keys, strip_key_prefixes=prefixes, strip_key_suffixes=suffixes)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _strip_keys(item, strip_keys=keys, strip_key_prefixes=prefixes, strip_key_suffixes=suffixes)


def _strip_key_name(node: Any, key_name: str) -> None:
    """Recursively remove the given key name from any dicts in the structure."""
    if key_name:
        _strip_keys(node, strip_keys={key_name})


class MCPToolResultAdapter:
    """Base adapter for normalizing MCP tool responses."""

    strip_keys: Tuple[str, ...] = ()
    strip_key_prefixes: Tuple[str, ...] = ()
    strip_key_suffixes: Tuple[str, ...] = ()
    server_name: Optional[str] = None
    tool_name: Optional[str] = None

    def matches(self, server_name: str, tool_name: str) -> bool:
        server_match = self.server_name is None or self.server_name == server_name
        tool_match = self.tool_name is None or self.tool_name == tool_name
        return server_match and tool_match

    def strip_payload(self, payload: Any) -> Any:
        """Apply configured key stripping to the payload in-place."""
        if self.strip_keys or self.strip_key_prefixes or self.strip_key_suffixes:
            _strip_keys(
                payload,
                strip_keys=set(self.strip_keys),
                strip_key_prefixes=self.strip_key_prefixes,
                strip_key_suffixes=self.strip_key_suffixes,
            )
        return payload

    def adapt(self, result: Any) -> Any:
        """Override to mutate/normalize the tool result."""
        return result


class BrightDataAdapterBase(MCPToolResultAdapter):
    """Shared helpers for Bright Data adapters."""

    def _extract_json_payload(self, result: Any) -> Optional[Tuple[Any, Any]]:
        content_blocks = getattr(result, "content", None)
        if not content_blocks or not isinstance(content_blocks, (list, tuple)):
            return None

        try:
            first_block = content_blocks[0]
        except IndexError:
            return None

        raw_text = getattr(first_block, "text", None)
        if not raw_text or not isinstance(raw_text, str):
            return None

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return None

        return first_block, payload


class BrightDataSearchEngineAdapter(BrightDataAdapterBase):
    """Strip heavy fields from Bright Data search responses."""

    server_name = "brightdata"
    tool_name = "search_engine"
    strip_keys = ("image", "image_base64")

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        self.strip_payload(payload)
        first_block.text = json.dumps(payload, ensure_ascii=False)
        return result


class BrightDataLinkedInCompanyProfileAdapter(BrightDataAdapterBase):
    """Strip HTML blobs from Bright Data LinkedIn company profiles."""

    server_name = "brightdata"
    tool_name = "web_data_linkedin_company_profile"
    strip_key_suffixes = ("_html",)

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        self.strip_payload(payload)
        first_block.text = json.dumps(payload, ensure_ascii=False)
        return result


class BrightDataLinkedInPersonProfileAdapter(BrightDataAdapterBase):
    """Adapter scaffold for Bright Data LinkedIn person profiles."""

    server_name = "brightdata"
    tool_name = "web_data_linkedin_person_profile"
    strip_keys = (
        "description_html",
        "company_logo_url",
        "institute_logo_url",
        "banner_image",
        "default_avatar",
        "image_url",
        "image",
        "img",
        "people_also_viewed",
    )
    strip_key_suffixes = ("_html", "_img")

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        self.strip_payload(payload)
        first_block.text = json.dumps(payload, ensure_ascii=False)
        return result


class BrightDataSearchEngineBatchAdapter(BrightDataAdapterBase):
    """Strip heavy fields from Bright Data batched search responses."""

    server_name = "brightdata"
    tool_name = "search_engine_batch"
    strip_keys = ("image", "image_base64")

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        self.strip_payload(payload)
        first_block.text = json.dumps(payload, ensure_ascii=False)
        return result


class BrightDataScrapeAsMarkdownAdapter(BrightDataAdapterBase):
    """Strip embedded data images from markdown snapshots."""

    server_name = "brightdata"
    tool_name = "scrape_as_markdown"

    def adapt(self, result: Any) -> Any:
        try:
            first_block = result.content[0]
            raw_text = first_block.text
        except (AttributeError, IndexError, TypeError):
            return result

        if isinstance(raw_text, str):
            first_block.text = scrub_markdown_data_images(raw_text)
        return result


class BrightDataScrapeBatchAdapter(BrightDataAdapterBase):
    """Strip embedded data images from batched markdown snapshots."""

    server_name = "brightdata"
    tool_name = "scrape_batch"

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        if isinstance(payload, list):
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                content = entry.get("content")
                if isinstance(content, str):
                    entry["content"] = scrub_markdown_data_images(content)

        first_block.text = json.dumps(payload)
        return result


class BrightDataAmazonProductSearchAdapter(BrightDataAdapterBase):
    """Limit Bright Data Amazon product search results."""

    server_name = "brightdata"
    tool_name = "web_data_amazon_product_search"

    def adapt(self, result: Any) -> Any:
        try:
            settings = get_tool_settings_for_owner(_RESULT_OWNER_CONTEXT.get())
        except DatabaseError:
            logger.error("Failed to load tool settings for Bright Data result limit.", exc_info=True)
            return result

        limit = getattr(settings, "brightdata_amazon_product_search_limit", None)
        if not isinstance(limit, int) or limit <= 0:
            return result

        if isinstance(getattr(result, "data", None), list):
            if len(result.data) > limit:
                result.data = result.data[:limit]
            return result

        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        if isinstance(payload, list) and len(payload) > limit:
            first_block.text = json.dumps(payload[:limit])

        return result


class MCPResultAdapterRegistry:
    """Registry of adapters keyed by provider/tool."""

    def __init__(self, adapters: Optional[List[MCPToolResultAdapter]] = None):
        self._adapters = list(adapters or [])

    @classmethod
    def default(cls) -> "MCPResultAdapterRegistry":
        return cls(
            adapters=[
                BrightDataSearchEngineAdapter(),
                BrightDataLinkedInCompanyProfileAdapter(),
                BrightDataLinkedInPersonProfileAdapter(),
                BrightDataSearchEngineBatchAdapter(),
                BrightDataScrapeAsMarkdownAdapter(),
                BrightDataScrapeBatchAdapter(),
                BrightDataAmazonProductSearchAdapter(),
            ]
        )

    def adapt(self, server_name: str, tool_name: str, result: Any) -> Any:
        for adapter in self._adapters:
            if adapter.matches(server_name, tool_name):
                try:
                    return adapter.adapt(result)
                except Exception:
                    logger.exception(
                        "Failed to adapt MCP result with %s for %s/%s",
                        adapter.__class__.__name__,
                        server_name,
                        tool_name,
                    )
                    return result
        return result
