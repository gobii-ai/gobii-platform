from typing import Any, Callable, Dict, Tuple

from api.integrations.google.auth import resolve_binding
from api.integrations.google.docs import (
    append_text,
    create_document,
    describe_current_user,
    find_document,
    get_document_content,
    insert_table,
    replace_all_text,
)
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


def _definition_get_document_content() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_docs_get_document_content",
            "description": (
                "Read the text content of an existing Google Doc. Returns the document title and plain text. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "The Google Doc document ID."},
                },
                "required": ["document_id"],
            },
        },
    }


def _definition_append_text() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_docs_append_text",
            "description": (
                "Append text to the end of an existing Google Doc. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "The Google Doc document ID."},
                    "text": {"type": "string", "description": "The text to append to the document."},
                },
                "required": ["document_id", "text"],
            },
        },
    }


def _definition_replace_all_text() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_docs_replace_all_text",
            "description": (
                "Find and replace all occurrences of text in a Google Doc. "
                "Useful for templates with placeholders like '[Month Value]' or '{{name}}'. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "The Google Doc document ID."},
                    "find_text": {"type": "string", "description": "The text to find (e.g., '[Month Value]')."},
                    "replace_text": {"type": "string", "description": "The text to replace it with."},
                    "match_case": {"type": "boolean", "description": "Whether to match case (default: true)."},
                },
                "required": ["document_id", "find_text", "replace_text"],
            },
        },
    }


def _definition_insert_table() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_docs_insert_table",
            "description": (
                "Insert a table at the end of a Google Doc. Optionally populate cells with initial data. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "The Google Doc document ID."},
                    "rows": {"type": "integer", "description": "Number of rows (default: 3)."},
                    "columns": {"type": "integer", "description": "Number of columns (default: 3)."},
                    "data": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "description": "Optional 2D array of cell values, e.g. [['Header1', 'Header2'], ['Row1Col1', 'Row1Col2']].",
                    },
                },
                "required": ["document_id"],
            },
        },
    }


DOCS_TOOL_DEFINITIONS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "google_docs_create_document": _definition_create_document,
    "google_docs_find_document": _definition_find_document,
    "google_docs_get_current_user": _definition_get_current_user,
    "google_docs_get_document_content": _definition_get_document_content,
    "google_docs_append_text": _definition_append_text,
    "google_docs_replace_all_text": _definition_replace_all_text,
    "google_docs_insert_table": _definition_insert_table,
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


def _execute_get_document_content(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent, scope_tier="minimal")
    if action:
        return action
    return get_document_content(bundle, params)


def _execute_append_text(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent, scope_tier="minimal")
    if action:
        return action
    return append_text(bundle, params)


def _execute_replace_all_text(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent, scope_tier="minimal")
    if action:
        return action
    return replace_all_text(bundle, params)


def _execute_insert_table(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent, scope_tier="minimal")
    if action:
        return action
    return insert_table(bundle, params)


DOCS_TOOL_EXECUTORS: Dict[str, Callable[[PersistentAgent, Dict[str, Any]], Dict[str, Any]]] = {
    "google_docs_create_document": _execute_create_document,
    "google_docs_find_document": _execute_find_document,
    "google_docs_get_current_user": _execute_get_current_user,
    "google_docs_get_document_content": _execute_get_document_content,
    "google_docs_append_text": _execute_append_text,
    "google_docs_replace_all_text": _execute_replace_all_text,
    "google_docs_insert_table": _execute_insert_table,
}


def get_docs_tool_definition(tool_name: str) -> Dict[str, Any]:
    return DOCS_TOOL_DEFINITIONS[tool_name]()


def execute_docs_tool(agent: PersistentAgent, params: Dict[str, Any], *, tool_name: str) -> Dict[str, Any]:
    executor = DOCS_TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return {"status": "error", "message": f"Doc tool '{tool_name}' is not supported"}
    return executor(agent, params)
