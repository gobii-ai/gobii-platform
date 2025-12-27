import contextlib
import contextvars
import json
import logging
import re
from typing import Any, List, Optional, Tuple

from django.db import DatabaseError

from api.agent.tools.content_skeleton import (
    extract_serp_skeleton,
    extract_skeleton,
    ContentSkeleton,
)
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


def _strip_image_fields(entry: dict[str, Any]) -> None:
    entry.pop("image", None)
    entry.pop("image_base64", None)
    images = entry.get("images")
    if isinstance(images, list):
        for image_entry in images:
            if isinstance(image_entry, dict):
                image_entry.pop("image", None)
                image_entry.pop("image_base64", None)


def _strip_key_name(node: Any, key_name: str) -> None:
    """Recursively remove the given key name from any dicts in the structure."""
    if not key_name:
        return

    if isinstance(node, (list, tuple)):
        for item in node:
            _strip_key_name(item, key_name)
    elif isinstance(node, dict):
        if key_name in node:
            node.pop(key_name, None)
        for value in node.values():
            _strip_key_name(value, key_name)


class MCPToolResultAdapter:
    """Base adapter for normalizing MCP tool responses."""

    server_name: Optional[str] = None
    tool_name: Optional[str] = None

    def matches(self, server_name: str, tool_name: str) -> bool:
        server_match = self.server_name is None or self.server_name == server_name
        tool_match = self.tool_name is None or self.tool_name == tool_name
        return server_match and tool_match

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


_SERP_INDICATORS = ("google search", "search results", "skip to main content")


def _parse_markdown_serp(markdown: str, query: str = "") -> List[dict]:
    """Extract search results from markdown SERP using ContentSkeleton.

    Uses the unified skeleton extractor for smarter title handling
    (URL fallback when title is "Read more" etc.).
    """
    skeleton = extract_serp_skeleton(markdown, query)

    # Convert skeleton format (t/u/p) to adapter format (title/link/position)
    return [
        {
            "title": item["t"],
            "link": item["u"],
            "position": item["p"],
        }
        for item in skeleton.items
    ]


class BrightDataSearchEngineAdapter(BrightDataAdapterBase):
    """Transform search results into universal skeleton format.

    Output: {kind: "serp", items: [{t, u, p}...], _meta: {...}}

    The insight: SERP and scraped pages use the SAME structure.
    Agent learns ONE pattern: json_each(result_json, '$.items')
    """

    server_name = "brightdata"
    tool_name = "search_engine"

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        original_bytes = len(first_block.text.encode('utf-8'))

        # Case 1: Already structured JSON with organic array - convert to skeleton
        organic_results = payload.get("organic")
        if isinstance(organic_results, list) and organic_results:
            for item in organic_results:
                if isinstance(item, dict):
                    _strip_image_fields(item)
            # Convert organic format to skeleton format
            items = [
                {"t": item.get("title", "")[:100], "u": item.get("link", "")[:300], "p": item.get("position", i+1)}
                for i, item in enumerate(organic_results[:12])
                if isinstance(item, dict)
            ]
            skeleton_output = {
                "kind": "serp",
                "items": items,
                "status": payload.get("status"),
            }
            if original_bytes > 1000:
                skeleton_bytes = len(json.dumps(skeleton_output).encode('utf-8'))
                skeleton_output["_meta"] = {
                    "original_bytes": original_bytes,
                    "ratio": f"{100 * (1 - skeleton_bytes / original_bytes):.0f}%",
                }
            first_block.text = json.dumps(skeleton_output, ensure_ascii=False)
            return result

        # Case 2: Markdown SERP in $.result - extract to skeleton
        markdown_content = payload.get("result")
        if isinstance(markdown_content, str) and len(markdown_content) > 500:
            lower_content = markdown_content[:2000].lower()
            if any(ind in lower_content for ind in _SERP_INDICATORS):
                skeleton = extract_serp_skeleton(markdown_content)
                if skeleton.items:
                    skeleton_output = {
                        "kind": "serp",
                        "items": skeleton.items,
                        "status": payload.get("status"),
                    }
                    if original_bytes > 1000:
                        skeleton_bytes = len(json.dumps(skeleton_output).encode('utf-8'))
                        skeleton_output["_meta"] = {
                            "original_bytes": original_bytes,
                            "ratio": f"{100 * (1 - skeleton_bytes / original_bytes):.0f}%",
                        }
                    first_block.text = json.dumps(skeleton_output, ensure_ascii=False)
                    return result

        first_block.text = json.dumps(payload, ensure_ascii=False)
        return result


class BrightDataLinkedInCompanyProfileAdapter(BrightDataAdapterBase):
    """Strip HTML blobs from Bright Data LinkedIn company profiles."""

    server_name = "brightdata"
    tool_name = "web_data_linkedin_company_profile"

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed

        def strip_updates(node: Any):
            if isinstance(node, list):
                for item in node:
                    strip_updates(item)
            elif isinstance(node, dict):
                updates = node.get("updates")
                if isinstance(updates, list):
                    for update in updates:
                        if isinstance(update, dict):
                            update.pop("text_html", None)
                for value in node.values():
                    strip_updates(value)

        strip_updates(payload)
        first_block.text = json.dumps(payload, ensure_ascii=False)
        return result


class BrightDataLinkedInPersonProfileAdapter(BrightDataAdapterBase):
    """Adapter scaffold for Bright Data LinkedIn person profiles."""

    server_name = "brightdata"
    tool_name = "web_data_linkedin_person_profile"

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        fields_to_strip = {
            "description_html",
            "company_logo_url",
            "institute_logo_url",
            "banner_image",
            "default_avatar",
            "image_url",
            "image",
            "img",
            
            # Network Fields
            "people_also_viewed",
        }

        def strip_fields(node: Any):
            if isinstance(node, list):
                for item in node:
                    strip_fields(item)
            elif isinstance(node, dict):
                # Remove matching keys before recursing into values
                for key in list(node.keys()):
                    if (
                        key in fields_to_strip
                        or key.endswith("_html")
                        or key.endswith("_img")
                    ):
                        node.pop(key, None)
                for value in node.values():
                    strip_fields(value)

        strip_fields(payload)
        first_block.text = json.dumps(payload, ensure_ascii=False)
        return result


class BrightDataSearchEngineBatchAdapter(BrightDataAdapterBase):
    """Strip heavy fields from Bright Data batched search responses."""

    server_name = "brightdata"
    tool_name = "search_engine_batch"

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                results = item.get("result")
                if isinstance(results, dict):
                    organic_results = results.get("organic")
                    if isinstance(organic_results, list):
                        for entry in organic_results:
                            if isinstance(entry, dict):
                                _strip_image_fields(entry)

                    related_results = results.get("related")
                    if isinstance(related_results, list):
                        for entry in related_results:
                            if isinstance(entry, dict):
                                _strip_image_fields(entry)

        first_block.text = json.dumps(payload, ensure_ascii=False)
        return result


def _extract_page_title(markdown: str) -> str:
    """Extract title from markdown - first h1 or first line."""
    for line in markdown.split('\n')[:20]:
        stripped = line.strip()
        if stripped.startswith('# '):
            return stripped[2:].strip()[:100]
    return ""


def _skeleton_to_compact_output(skeleton: ContentSkeleton, original_bytes: int) -> dict:
    """Convert skeleton to compact output format for agent consumption.

    Output format optimized for querying:
        - sections: [{h: heading, c: content_preview}] for articles
        - items: [{t: title, u: url}] for lists/serp
        - excerpt: raw text fallback
        - _meta: compression stats
    """
    output = {
        "kind": skeleton.kind,
        "title": skeleton.title,
    }

    if skeleton.items:
        output["items"] = skeleton.items

    if skeleton.excerpt:
        output["excerpt"] = skeleton.excerpt

    # Add compression stats
    skeleton_bytes = skeleton.byte_size()
    if original_bytes > 1000:
        output["_meta"] = {
            "original_bytes": original_bytes,
            "compressed_bytes": skeleton_bytes,
            "ratio": f"{100 * (1 - skeleton_bytes / original_bytes):.0f}%",
        }

    return output


# Noise patterns to strip before extraction
_NOISE_PATTERNS = [
    # Navigation/UI
    (r'^(Skip to|Jump to|Go to).*$', re.MULTILINE),
    (r'^\s*(Menu|Navigation|Search|Sign [Ii]n|Log [Ii]n|Subscribe)[\s|]*$', re.MULTILINE),
    (r'^\s*\[?(Home|About|Contact|Blog|Products|Services)\]?\s*$', re.MULTILINE),
    # Cookie/consent banners
    (r'(?i)cookie.*?(accept|preferences|consent).*?\n', 0),
    (r'(?i)we use cookies.*?\n', 0),
    # Social/share buttons
    (r'^\s*(Share|Tweet|Pin|Follow us).*$', re.MULTILINE),
    (r'^\s*\d+\s*(shares?|likes?|comments?)\s*$', re.MULTILINE | re.IGNORECASE),
    # Footer noise
    (r'(?i)^\s*(copyright|©|all rights reserved).*$', re.MULTILINE),
    (r'(?i)^\s*privacy policy.*$', re.MULTILINE),
    (r'(?i)^\s*terms (of|and) (service|use).*$', re.MULTILINE),
    # Repeated separators
    (r'[-─=]{10,}', 0),
    (r'\n{4,}', 0),
    # Empty brackets/parens (broken links)
    (r'\[\s*\]\(\s*\)', 0),
    (r'\(\s*\)', 0),
]


def _strip_noise(markdown: str) -> str:
    """Strip common noise patterns from markdown."""
    result = markdown
    for pattern, flags in _NOISE_PATTERNS:
        result = re.sub(pattern, '', result, flags=flags)
    # Collapse excessive whitespace
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


class BrightDataScrapeAsMarkdownAdapter(BrightDataAdapterBase):
    """Transform messy scraped markdown into clean, structured skeleton.

    The insight: we don't need raw messy markdown.
    We need structured content the agent can query.

    Input:  17KB of nasty random web garbage
    Output: 800 bytes of clean {kind, title, items[], excerpt}
    """

    server_name = "brightdata"
    tool_name = "scrape_as_markdown"

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            # Fallback: just scrub images from raw text
            try:
                first_block = result.content[0]
                if isinstance(first_block.text, str):
                    first_block.text = scrub_markdown_data_images(first_block.text)
            except (AttributeError, IndexError, TypeError):
                pass
            return result

        first_block, payload = parsed
        markdown_content = payload.get("result")

        if not isinstance(markdown_content, str) or len(markdown_content) < 100:
            first_block.text = json.dumps(payload, ensure_ascii=False)
            return result

        original_bytes = len(markdown_content.encode('utf-8'))

        # Step 1: Scrub data images (huge base64 blobs)
        cleaned = scrub_markdown_data_images(markdown_content)

        # Step 2: Strip noise patterns (nav, cookies, footers)
        cleaned = _strip_noise(cleaned)

        # Step 3: Extract title
        title = _extract_page_title(cleaned) or payload.get("url", "")[:80]

        # Step 4: Extract skeleton structure
        skeleton = extract_skeleton(cleaned, title=title)

        # Step 5: Build compact output
        compact_output = _skeleton_to_compact_output(skeleton, original_bytes)
        compact_output["status"] = payload.get("status")
        compact_output["url"] = payload.get("url")

        first_block.text = json.dumps(compact_output, ensure_ascii=False)
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
