"""Synthetic tool definitions used by deterministic eval agents.

These tools let eval scenarios test tool choice without depending on external
integration catalog state such as Pipedream app selection or OAuth setup.
"""

from typing import Any, Dict

from api.agent.eval_agents import is_eval_agent
from api.agent.system_skills.image_generation import IMAGE_GENERATION_SYSTEM_SKILL_KEY
from api.agent.tools.create_image import get_create_image_tool
from api.models import PersistentAgent

EVAL_SYNTHETIC_TOOL_SERVER = "eval"

_APOLLO_ACCOUNT_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "industry": {"type": "string"},
        "location": {"type": "string"},
        "employee_count_min": {"type": "integer"},
        "employee_count_max": {"type": "integer"},
    },
    "additionalProperties": True,
}

_APOLLO_CONTACT_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "person_titles": {
            "type": "array",
            "items": {"type": "string"},
        },
        "organization_locations": {
            "type": "array",
            "items": {"type": "string"},
        },
        "organization_industries": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "additionalProperties": True,
}

_APOLLO_PEOPLE_ENRICHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "email": {"type": "string"},
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "organization_name": {"type": "string"},
    },
    "additionalProperties": True,
}

_GENERIC_WEB_DATA_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "url": {"type": "string"},
        "keyword": {"type": "string"},
        "prompt": {"type": "string"},
    },
    "additionalProperties": True,
}

_GENERIC_BATCH_WORK_SCHEMA = {
    "type": "object",
    "properties": {
        "batch_size": {"type": "integer"},
        "limit": {"type": "integer"},
        "status": {"type": "string"},
        "query": {"type": "string"},
    },
    "additionalProperties": True,
}

_WEB_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
    },
    "required": ["query"],
    "additionalProperties": False,
}

_LINKEDIN_PEOPLE_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "company": {"type": "string"},
        "title": {"type": "string"},
        "location": {"type": "string"},
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "additionalProperties": True,
}

_GENERIC_SPREADSHEET_SCHEMA = {
    "type": "object",
    "properties": {
        "spreadsheet_id": {"type": "string"},
        "spreadsheetId": {"type": "string"},
        "worksheet": {"type": "string"},
        "worksheetId": {"type": "string"},
        "range": {"type": "string"},
        "row": {"type": "object", "additionalProperties": True},
        "rows": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        },
        "query": {"type": "string"},
    },
    "additionalProperties": True,
}

_GOOGLE_SHEETS_TOOL_DESCRIPTIONS = {
    "google_sheets-get-values-in-range": "Read values from a Google Sheets range.",
    "google_sheets-find-row": "Find rows in Google Sheets matching criteria; use for requests like find the row where a column equals a value.",
    "google_sheets-add-single-row": "Add one row to a Google Sheets worksheet.",
    "google_sheets-add-multiple-rows": "Add multiple rows to a Google Sheets worksheet; use for requests to add several rows/prospects/items.",
    "google_sheets-update-cell": "Update one Google Sheets cell.",
    "google_sheets-update-row": "Update a matching Google Sheets row.",
    "google_sheets-update-multiple-rows": "Update multiple Google Sheets rows.",
    "google_sheets-upsert-row": "Insert or update a Google Sheets row by key.",
    "google_sheets-list-worksheets": "List worksheets in a Google Sheets spreadsheet.",
    "google_sheets-get-spreadsheet-info": "Get Google Sheets spreadsheet metadata; use when the user asks for spreadsheet info.",
    "google_sheets-create-spreadsheet": "Create a Google Sheets spreadsheet.",
    "google_sheets-read-rows": "Read rows from a Google Sheets worksheet.",
    "google_sheets-get-spreadsheet-by-id": "Open a Google Sheets spreadsheet by ID when asked to open/get by ID; for spreadsheet info metadata, use google_sheets-get-spreadsheet-info.",
    "google_sheets-get-current-user": "Return the connected Google Sheets account user.",
    "google_sheets-add-rows": "Append rows to a Google Sheets worksheet.",
}

_CREATE_IMAGE_FUNCTION = get_create_image_tool()["function"]

EVAL_SYNTHETIC_TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "create_image": {
        "description": _CREATE_IMAGE_FUNCTION["description"],
        "parameters": _CREATE_IMAGE_FUNCTION["parameters"],
        "system_skill_key": IMAGE_GENERATION_SYSTEM_SKILL_KEY,
    },
    "apollo_io-search-contacts": {
        "description": "Search Apollo.io for people and contacts matching lead criteria.",
        "parameters": _APOLLO_CONTACT_SEARCH_SCHEMA,
    },
    "apollo_io-search-accounts": {
        "description": "Search Apollo.io for companies and accounts matching firmographic criteria.",
        "parameters": _APOLLO_ACCOUNT_SEARCH_SCHEMA,
    },
    "apollo_io-people-enrichment": {
        "description": "Enrich a person profile from Apollo.io using email or identity details.",
        "parameters": _APOLLO_PEOPLE_ENRICHMENT_SCHEMA,
    },
    "mcp_brightdata_search_engine": {
        "description": "Search deterministic eval web snippets; returned .example.test URLs are valid source URLs.",
        "parameters": _WEB_SEARCH_SCHEMA,
    },
    "mcp_brightdata_scrape_as_markdown": {
        "description": "Scrape deterministic eval pages; returned .example.test URLs are valid source URLs.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_linkedin_person_profile": {
        "description": "Fetch structured LinkedIn person profile data for a known person or profile URL; prefer this over generic web search when title, headline, or location are needed.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_linkedin_company_profile": {
        "description": "Fetch structured LinkedIn company profile data; prefer this over generic web search for LinkedIn company industry, size, or profile details.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_linkedin_job_listings": {
        "description": "Fetch structured LinkedIn job listing data; prefer this over generic web search for LinkedIn role lists and hiring details. Accept a known company, URL, or category query; if the user asks for a representative category such as a fintech company, proceed with the category or a reasonable representative instead of asking which company.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_linkedin_people_search": {
        "description": "Search LinkedIn people data with structured criteria when a profile URL is unknown; use this before person-profile lookup for name/company searches.",
        "parameters": _LINKEDIN_PEOPLE_SEARCH_SCHEMA,
    },
    "mcp_brightdata_web_data_linkedin_posts": {
        "description": "Fetch structured LinkedIn post data; prefer this over generic web search for recent LinkedIn posts or updates.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_amazon_product": {
        "description": "Fetch structured Amazon product data.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_instagram_profiles": {
        "description": "Fetch structured Instagram profile data.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_reddit_posts": {
        "description": "Fetch Reddit posts; prefer this over browser automation for Reddit mentions or sentiment.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_google_maps_reviews": {
        "description": (
            "Fetch structured Google Maps review data by business, category, or local-market query. "
            "For local lead screens, use this directly; if the user omits a city, choose a representative market "
            "or broad category query instead of asking which city."
        ),
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_yahoo_finance_business": {
        "description": "Fetch structured Yahoo Finance business data.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "eval_send_outreach_batch": {
        "description": (
            "Deterministic eval tool for sending the next bounded outreach batch from an already-approved queue. "
            "Use this directly; do not call search_tools first. "
            "The result may include remaining_work or next_cursor; if work remains and no schedule exists, continue "
            "bounded work or set a resume schedule before stopping."
        ),
        "parameters": _GENERIC_BATCH_WORK_SCHEMA,
    },
    "eval_verify_candidate_batch": {
        "description": (
            "Deterministic eval tool for verifying a bounded batch of sourcing candidates against location, company, "
            "and tenure constraints. Use this directly; do not call search_tools first. Partial results may include "
            "remaining_work or next_cursor."
        ),
        "parameters": _GENERIC_BATCH_WORK_SCHEMA,
    },
    "eval_prepare_next_batch": {
        "description": (
            "Deterministic eval tool for preparing a bounded follow-up batch. Use this directly; do not call "
            "search_tools first. If it says to wait for the next scheduled run while returning remaining_work, "
            "that guidance only makes sense when a schedule exists or is being set."
        ),
        "parameters": _GENERIC_BATCH_WORK_SCHEMA,
    },
    **{
        tool_name: {
            "description": (
                "Currently enabled Google Sheets tool. "
                f"Use this directly; do not call search_tools first. {description}"
            ),
            "parameters": _GENERIC_SPREADSHEET_SCHEMA,
        }
        for tool_name, description in _GOOGLE_SHEETS_TOOL_DESCRIPTIONS.items()
    },
}


def is_eval_synthetic_tool_name(tool_name: str) -> bool:
    return tool_name in EVAL_SYNTHETIC_TOOL_DEFINITIONS


def get_eval_synthetic_tool_definition(agent: PersistentAgent, tool_name: str) -> Dict[str, Any] | None:
    if not is_eval_agent(agent):
        return None
    metadata = EVAL_SYNTHETIC_TOOL_DEFINITIONS.get(tool_name)
    if not metadata:
        return None
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": metadata["description"],
            "parameters": metadata["parameters"],
        },
    }


def get_available_eval_synthetic_tool_catalog(agent: PersistentAgent) -> list[Dict[str, Any]]:
    if not is_eval_agent(agent):
        return []
    return [
        {
            "full_name": tool_name,
            "description": metadata["description"],
            "parameters": metadata["parameters"],
        }
        for tool_name, metadata in EVAL_SYNTHETIC_TOOL_DEFINITIONS.items()
    ]


def get_eval_synthetic_tool_fallback_result(tool_name: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Return empty eval data when a scenario did not provide a fixture."""
    params = params or {}
    content: Dict[str, Any] = {"fixture_configured": False}

    if tool_name == "create_image":
        file_path = str(params.get("file_path") or "/exports/eval-image.png")
        file_ref = f"$[{file_path}]"
        source_images = params.get("source_images") or []
        return {
            "status": "ok",
            "file": file_ref,
            "inline": f"![Generated image]({file_ref})",
            "inline_html": f"<img src='{file_ref}' alt='Generated image' />",
            "attach": file_ref,
            "source_image_count": 1 if isinstance(source_images, str) else len(source_images),
            "eval_fixture": True,
        }
    if tool_name.startswith("google_sheets-"):
        content.update(
            {
                "spreadsheet_id": params.get("spreadsheet_id") or params.get("spreadsheetId"),
                "worksheet": params.get("worksheet") or params.get("worksheetId"),
                "range": params.get("range"),
                "rows": [],
                "values": [],
                "worksheets": [],
                "match_count": 0,
            }
        )
        message = "No deterministic Google Sheets eval fixture returned data."
    elif tool_name.startswith("apollo_io-"):
        content.update(
            {
                "people": [],
                "contacts": [],
                "accounts": [],
                "match_count": 0,
            }
        )
        message = "No deterministic Apollo eval fixture returned data."
    elif tool_name == "mcp_brightdata_search_engine":
        content.update({"results": [], "match_count": 0})
        message = "No deterministic web-search eval fixture returned data."
    elif tool_name == "mcp_brightdata_scrape_as_markdown":
        content.update({"url": params.get("url"), "markdown": ""})
        message = "No deterministic scrape eval fixture returned data."
    elif tool_name.startswith("mcp_brightdata_web_data_"):
        content.update({"items": [], "results": [], "match_count": 0})
        message = "No deterministic web-data eval fixture returned data."
    elif tool_name.startswith("eval_"):
        content.update({"items": [], "remaining_work": 0, "next_cursor": None})
        message = "No deterministic batch-work eval fixture returned data."
    else:
        content.update({"items": [], "match_count": 0})
        message = f"No deterministic eval fixture returned data for {tool_name}."

    return {
        "status": "ok",
        "tool": tool_name,
        "message": message,
        "content": content,
        "next_action": "Treat this as no data found; ask for missing required details instead of inferring them.",
    }
