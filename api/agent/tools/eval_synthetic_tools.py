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
