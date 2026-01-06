from typing import Any, Callable, Dict, Tuple

from api.integrations.google.auth import resolve_binding
from api.integrations.google.docs import create_document, describe_current_user, find_document
from api.models import PersistentAgent


def _resolve_or_action(agent: PersistentAgent, scope_tier: str) -> Tuple[Any, Dict[str, Any] | None]:
    bundle, action = resolve_binding(agent, required_scope_tier=scope_tier)
    if action:
        return None, action
    return bundle, None


def _definition_create_document() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_docs_create_document",
            "description": (
                "Create a Google Doc with an optional title and initial body content. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Document title."},
                    "body": {"type": "string", "description": "Optional initial text to insert at the top of the document."},
                },
            },
        },
    }


def _definition_find_document() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_docs_find_document",
            "description": (
                "Find Google Docs by name using Drive metadata search. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for in document names."},
                    "limit": {"type": "integer", "description": "Maximum number of results to return."},
                },
                "required": ["query"],
            },
        },
    }


def _definition_get_current_user() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_docs_get_current_user",
            "description": (
                "Return the connected Google account email for the current agent binding. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    }


DOCS_TOOL_DEFINITIONS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "google_docs_create_document": _definition_create_document,
    "google_docs_find_document": _definition_find_document,
    "google_docs_get_current_user": _definition_get_current_user,
}


def _execute_create_document(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent, scope_tier="minimal")
    if action:
        return action
    return create_document(bundle, params)


def _execute_find_document(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent, scope_tier="search_enabled")
    if action:
        return action
    return find_document(bundle, params)


def _execute_get_current_user(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent, scope_tier="minimal")
    if action:
        return action
    return describe_current_user(bundle)


DOCS_TOOL_EXECUTORS: Dict[str, Callable[[PersistentAgent, Dict[str, Any]], Dict[str, Any]]] = {
    "google_docs_create_document": _execute_create_document,
    "google_docs_find_document": _execute_find_document,
    "google_docs_get_current_user": _execute_get_current_user,
}


def get_docs_tool_definition(tool_name: str) -> Callable[[], Dict[str, Any]]:
    return DOCS_TOOL_DEFINITIONS[tool_name]


def execute_docs_tool(agent: PersistentAgent, params: Dict[str, Any], *, tool_name: str) -> Dict[str, Any]:
    executor = DOCS_TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return {"status": "error", "message": f"Doc tool '{tool_name}' is not supported"}
    return executor(agent, params)
