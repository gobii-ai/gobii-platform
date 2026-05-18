"""Synthetic tool definitions used by deterministic eval agents.

These tools let eval scenarios test tool choice without depending on external
integration catalog state such as Pipedream app selection or OAuth setup.
"""

from typing import Any, Dict

from api.agent.eval_agents import is_eval_agent
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

EVAL_SYNTHETIC_TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
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
        "description": "Search the web and return relevant result snippets for research tasks.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_scrape_as_markdown": {
        "description": "Scrape a known web page and return its content as markdown; use when the user says to scrape a docs/help/pricing/blog page.",
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
        "description": "Fetch structured Reddit post data.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_google_maps_reviews": {
        "description": "Fetch structured Google Maps review data.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
    },
    "mcp_brightdata_web_data_yahoo_finance_business": {
        "description": "Fetch structured Yahoo Finance business data.",
        "parameters": _GENERIC_WEB_DATA_SCHEMA,
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
