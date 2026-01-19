import json
import re
from typing import Any, Dict, List

WORK_TASK_ALLOWED_MCP_TOOLS: List[str] = [
    "mcp_brightdata_search_engine",
    "mcp_brightdata_scrape_as_markdown",
    "mcp_brightdata_web_data_amazon_product",
    "mcp_brightdata_web_data_amazon_product_search",
    "mcp_brightdata_web_data_linkedin_person_profile",
    "mcp_brightdata_web_data_linkedin_company_profile",
    "mcp_brightdata_web_data_linkedin_job_listings",
    "mcp_brightdata_web_data_linkedin_posts",
    "mcp_brightdata_web_data_linkedin_people_search",
]


def extract_mcp_server_name(tool_name: str) -> str:
    if not tool_name:
        return ""
    parts = tool_name.split("_", 2)
    if len(parts) < 2:
        return ""
    if parts[0] != "mcp":
        return ""
    return parts[1]


def build_work_task_tool_result(
    *,
    task_id: str,
    status: str,
    result_summary: Any = None,
    error_message: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "task_id": task_id,
    }
    if result_summary is not None:
        payload["result_summary"] = result_summary
    if error_message:
        payload["message"] = error_message
    return payload


def _extract_urls(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r'https?://[^\s\]\)\}"]+', text)


def coerce_summary_payload(summary_text: str) -> Dict[str, Any]:
    if not summary_text:
        return {"summary": "", "citations": []}
    try:
        parsed = json.loads(summary_text)
        if isinstance(parsed, dict) and "summary" in parsed:
            if "citations" not in parsed:
                parsed["citations"] = []
            return parsed
    except Exception:
        pass

    urls = _extract_urls(summary_text)
    citations = [{"url": url} for url in urls]
    return {"summary": summary_text, "citations": citations}


def serialize_tool_result(payload: Dict[str, Any]) -> str:
    try:
        return json.dumps(payload)
    except (TypeError, ValueError):
        return json.dumps(payload, default=str)
