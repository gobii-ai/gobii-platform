from typing import Any, Callable, Dict, Tuple

from api.integrations.google.auth import resolve_binding
from api.integrations.google.sheets import (
    append_values,
    clear_range,
    create_spreadsheet,
    create_worksheet,
    delete_rows,
    describe_current_user,
    find_rows,
    get_spreadsheet_info,
    get_values,
    list_worksheets,
    update_cell,
    update_values,
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
            "description": (
                "Create a new Google Sheets spreadsheet. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
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
            "description": (
                "Create a worksheet inside an existing spreadsheet. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
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
            "description": (
                "Append values to a range in a spreadsheet. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID."},
                    "range": {"type": "string", "description": "A1 notation range (e.g., Sheet1!A1:C1)."},
                    "values": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "2D list of values to append (each cell as a string)."},
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
            "description": (
                "Retrieve values from a range in a spreadsheet. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
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
            "description": (
                "Update a single cell or range in a spreadsheet. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
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
            "description": (
                "Return the connected Google account email for the current agent binding. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _def_get_spreadsheet_info() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_get_spreadsheet_info",
            "description": (
                "Get metadata about a spreadsheet including title and list of all sheets/tabs. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "The spreadsheet ID."},
                },
                "required": ["spreadsheet_id"],
            },
        },
    }


def _def_list_worksheets() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_list_worksheets",
            "description": (
                "List all worksheets/tabs in a spreadsheet. Returns sheet IDs, titles, and dimensions. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "The spreadsheet ID."},
                },
                "required": ["spreadsheet_id"],
            },
        },
    }


def _def_clear_range() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_clear_range",
            "description": (
                "Clear all values in a range (keeps formatting). Use this to reset data before writing new values. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "The spreadsheet ID."},
                    "range": {"type": "string", "description": "A1 notation range to clear (e.g., Sheet1!A1:D10)."},
                },
                "required": ["spreadsheet_id", "range"],
            },
        },
    }


def _def_find_rows() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_find_rows",
            "description": (
                "Find rows where a specific column matches a value. Returns matching row numbers and data. "
                "Useful for searching spreadsheets like 'find all rows where Name = John'. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "The spreadsheet ID."},
                    "range": {"type": "string", "description": "A1 notation range to search (e.g., Sheet1 or Sheet1!A:Z)."},
                    "search_column": {"type": "string", "description": "Column to search in. Can be letter (A, B, C) or 0-indexed number."},
                    "search_value": {"type": "string", "description": "Value to search for (case-insensitive)."},
                },
                "required": ["spreadsheet_id", "search_value"],
            },
        },
    }


def _def_delete_rows() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_delete_rows",
            "description": (
                "Delete one or more rows from a sheet. Requires the numeric sheet_id (from list_worksheets), not sheet name. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "The spreadsheet ID."},
                    "sheet_id": {"type": "integer", "description": "Numeric sheet ID (get from list_worksheets, not sheet name)."},
                    "start_row": {"type": "integer", "description": "Starting row index (0-indexed, so row 1 = 0)."},
                    "end_row": {"type": "integer", "description": "Ending row index, exclusive (optional, defaults to start_row + 1 for single row)."},
                },
                "required": ["spreadsheet_id", "sheet_id", "start_row"],
            },
        },
    }


def _def_update_values() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "google_sheets_update_values",
            "description": (
                "Update multiple cells in a range at once. More efficient than update_cell for bulk updates. "
                "If authorization is needed, this tool will return status=action_required with connect_url; "
                "always send that link to the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "The spreadsheet ID."},
                    "range": {"type": "string", "description": "A1 notation range to update (e.g., Sheet1!A1:C3)."},
                    "values": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "description": "2D array of values to write, e.g. [['A1', 'B1'], ['A2', 'B2']].",
                    },
                    "value_input_option": {
                        "type": "string",
                        "enum": ["RAW", "USER_ENTERED"],
                        "description": "How to interpret values (RAW = literal, USER_ENTERED = parse formulas/dates).",
                    },
                },
                "required": ["spreadsheet_id", "range", "values"],
            },
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
    "google_sheets_delete_worksheet",
    "google_sheets_get_cell",
    "google_sheets_get_sheet",
    "google_sheets_insert_anchored_note",
    "google_sheets_insert_comment",
    "google_sheets_insert_dimension",
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
    "google_sheets_get_spreadsheet_info": _def_get_spreadsheet_info,
    "google_sheets_list_worksheets": _def_list_worksheets,
    "google_sheets_clear_range": _def_clear_range,
    "google_sheets_find_rows": _def_find_rows,
    "google_sheets_delete_rows": _def_delete_rows,
    "google_sheets_update_values": _def_update_values,
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


def _execute_get_spreadsheet_info(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return get_spreadsheet_info(bundle, params)


def _execute_list_worksheets(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return list_worksheets(bundle, params)


def _execute_clear_range(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return clear_range(bundle, params)


def _execute_find_rows(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return find_rows(bundle, params)


def _execute_delete_rows(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return delete_rows(bundle, params)


def _execute_update_values(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    bundle, action = _resolve_or_action(agent)
    if action:
        return action
    return update_values(bundle, params)


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
    "google_sheets_get_spreadsheet_info": _execute_get_spreadsheet_info,
    "google_sheets_list_worksheets": _execute_list_worksheets,
    "google_sheets_clear_range": _execute_clear_range,
    "google_sheets_find_rows": _execute_find_rows,
    "google_sheets_delete_rows": _execute_delete_rows,
    "google_sheets_update_values": _execute_update_values,
}

# Register placeholder executors
for placeholder_name in PLACEHOLDER_TOOL_NAMES:
    SHEETS_TOOL_EXECUTORS[placeholder_name] = lambda agent, params, n=placeholder_name: _execute_placeholder(
        agent, params, tool_name=n
    )


def get_sheets_tool_definition(tool_name: str) -> Dict[str, Any]:
    return SHEETS_TOOL_DEFINITIONS[tool_name]()


def execute_sheets_tool(agent: PersistentAgent, params: Dict[str, Any], *, tool_name: str) -> Dict[str, Any]:
    executor = SHEETS_TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return {"status": "error", "message": f"Sheets tool '{tool_name}' is not supported"}
    return executor(agent, params)
