import json
import logging
from typing import Any, List, Optional, Tuple


logger = logging.getLogger(__name__)


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


class BrightDataSearchEngineAdapter(BrightDataAdapterBase):
    """Strip heavy fields from Bright Data search responses."""

    server_name = "brightdata"
    tool_name = "search_engine"

    def adapt(self, result: Any) -> Any:
        parsed = self._extract_json_payload(result)
        if not parsed:
            return result

        first_block, payload = parsed
        organic_results = payload.get("organic")
        if isinstance(organic_results, list):
            for item in organic_results:
                if isinstance(item, dict):
                    item.pop("image", None)
                    item.pop("image_base64", None)

        first_block.text = json.dumps(payload)
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
        first_block.text = json.dumps(payload)
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
                                entry.pop("image", None)
                                entry.pop("image_base64", None)

        first_block.text = json.dumps(payload)
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
                BrightDataSearchEngineBatchAdapter(),
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
