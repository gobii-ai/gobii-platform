from typing import Any, Callable, Dict, Tuple

from api.integrations.google.auth import resolve_binding
from api.integrations.google.sheets import (
    append_values,
    create_spreadsheet,
    create_worksheet,
    describe_current_user,
    get_values,
    update_cell,
)
from api.models import PersistentAgent


def _resolve_or_action(agent: PersistentAgent, scope_tier: str = "minimal") -> Tuple[Any, Dict[str, Any] | None]:
    bundle, action = resolve_binding(agent, required_scope_tier=scope_tier)
    if action:
        return None, action
    return bundle, None


def _def_not_implemented(name: str, description: str) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{description} (not yet implemented in first-party integration).",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _def_create_spreadsheet() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_create_spreadsheet",
            "description": "Create a new Google Sheets spreadsheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Spreadsheet title."},
                },
            },
        },
    }


def _def_create_worksheet() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_create_worksheet",
            "description": "Create a worksheet inside an existing spreadsheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Target spreadsheet ID."},
                    "title": {"type": "string", "description": "Worksheet title."},
                    "row_count": {"type": "integer", "description": "Initial row count."},
                    "column_count": {"type": "integer", "description": "Initial column count."},
                },
                "required": ["spreadsheet_id", "title"],
            },
        },
    }


def _def_append_values() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_append_values",
            "description": "Append values to a range in a spreadsheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID."},
                    "range": {"type": "string", "description": "A1 notation range (e.g., Sheet1!A1:C1)."},
                    "values": {"type": "array", "items": {"type": "array"}, "description": "2D list of values to append."},
                    "value_input_option": {
                        "type": "string",
                        "enum": ["RAW", "USER_ENTERED"],
                        "description": "Value input option (RAW or USER_ENTERED).",
                    },
                },
                "required": ["spreadsheet_id", "range", "values"],
            },
        },
    }


def _def_get_values() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_get_values_in_range",
            "description": "Retrieve values from a range in a spreadsheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID."},
                    "range": {"type": "string", "description": "A1 notation range to read."},
                },
                "required": ["spreadsheet_id", "range"],
            },
        },
    }


def _def_update_cell() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_update_cell",
            "description": "Update a single cell or range in a spreadsheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID."},
                    "range": {"type": "string", "description": "A1 notation range to update."},
                    "value": {"type": "string", "description": "Value to write."},
                    "value_input_option": {
                        "type": "string",
                        "enum": ["RAW", "USER_ENTERED"],
                        "description": "Value input option (RAW or USER_ENTERED).",
                    },
                },
                "required": ["spreadsheet_id", "range", "value"],
            },
        },
    }


def _def_get_current_user() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_get_current_user",
            "description": "Return the connected Google account email for the current agent binding.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


PLACEHOLDER_TOOL_NAMES = [
    "google_sheets_add_column",
    "google_sheets_add_conditional_format_rule",
    "google_sheets_add_multiple_rows",
    "google_sheets_add_protected_range",
    "google_sheets_add_single_row",
    "google_sheets_clear_cell",
    "google_sheets_clear_rows",
    "google_sheets_copy_worksheet",
    "google_sheets_delete_rows",
    "google_sheets_delete_worksheet",
    "google_sheets_find_row",
    "google_sheets_get_cell",
    "google_sheets_get_sheet",
    "google_sheets_get_spreadsheet_by_id",
    "google_sheets_insert_anchored_note",
    "google_sheets_insert_comment",
    "google_sheets_insert_dimension",
    "google_sheets_list_worksheets",
    "google_sheets_move_dimension",
    "google_sheets_set_data_validation",
    "google_sheets_update_conditional_format_rule",
    "google_sheets_update_formatting",
    "google_sheets_update_multiple_rows",
    "google_sheets_update_row",
    "google_sheets_upsert_row",
]


SHEETS_TOOL_DEFINITIONS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "google_sheets_create_spreadsheet": _def_create_spreadsheet,
    "google_sheets_create_worksheet": _def_create_worksheet,
    "google_sheets_append_values": _def_append_values,
    "google_sheets_get_values_in_range": _def_get_values,
    "google_sheets_update_cell": _def_update_cell,
    "google_sheets_get_current_user": _def_get_current_user,
}

# Add placeholders
for placeholder_name in PLACEHOLDER_TOOL_NAMES:
    SHEETS_TOOL_DEFINITIONS[placeholder_name] = lambda n=placeholder_name: _def_not_implemented(
        n, f"{n.replace('_', ' ')}"
    )


def _execute_create_spreadsheet(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return create_spreadsheet(bundle, params)


def _execute_create_worksheet(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return create_worksheet(bundle, params)


def _execute_append_values(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return append_values(bundle, params)


def _execute_get_values(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return get_values(bundle, params)


def _execute_update_cell(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return update_cell(bundle, params)


def _execute_get_current_user(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return describe_current_user(bundle)


def _execute_placeholder(agent: PersistentAgent, params: Dict[str, Any], *, tool_name: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "message": f"The tool '{tool_name}' is not yet implemented in the first-party Google Sheets integration.",
    }


SHEETS_TOOL_EXECUTORS: Dict[str, Callable[[PersistentAgent, Dict[str, Any]], Dict[str, Any]]] = {
    "google_sheets_create_spreadsheet": _execute_create_spreadsheet,
    "google_sheets_create_worksheet": _execute_create_worksheet,
    "google_sheets_append_values": _execute_append_values,
    "google_sheets_get_values_in_range": _execute_get_values,
    "google_sheets_update_cell": _execute_update_cell,
    "google_sheets_get_current_user": _execute_get_current_user,
}

# Register placeholder executors
for placeholder_name in PLACEHOLDER_TOOL_NAMES:
    SHEETS_TOOL_EXECUTORS[placeholder_name] = lambda agent, params, n=placeholder_name: _execute_placeholder(
        agent, params, tool_name=n
    )


def get_sheets_tool_definition(tool_name: str) -> Callable[[], Dict[str, Any]]:
    return SHEETS_TOOL_DEFINITIONS[tool_name]


def execute_sheets_tool(agent: PersistentAgent, params: Dict[str, Any], *, tool_name: str) -> Dict[str, Any]:
    executor = SHEETS_TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return {"status": "error", "message": f"Sheets tool '{tool_name}' is not supported"}
    return executor(agent, params)
