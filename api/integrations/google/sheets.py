"""
Google Sheets helpers for first-party tools.
"""

import logging
from typing import Any, Dict, List

from api.integrations.google.auth import ensure_fresh_credentials

logger = logging.getLogger(__name__)

SHEETS_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{sheet_id}"


def _build_sheets_service(credentials):
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None, {
            "status": "error",
            "message": (
                "google-api-python-client is not installed. "
                "Install it to enable Google Sheets actions."
            ),
        }
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    return service, None


def create_spreadsheet(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    title = params.get("title") or "Untitled spreadsheet"
    body = {"properties": {"title": title}}
    try:
        resp = service.spreadsheets().create(body=body, fields="spreadsheetId,properties/title").execute()
        spreadsheet_id = resp.get("spreadsheetId")
        return {
            "status": "ok",
            "spreadsheet_id": spreadsheet_id,
            "title": resp.get("properties", {}).get("title"),
            "url": SHEETS_URL_TEMPLATE.format(sheet_id=spreadsheet_id) if spreadsheet_id else "",
        }
    except Exception as exc:  # pragma: no cover - external API
        logger.error("Sheets create_spreadsheet failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def create_worksheet(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    title = params.get("title") or "Sheet 1"
    row_count = params.get("row_count") or 100
    column_count = params.get("column_count") or 26
    try:
        row_count = int(row_count)
        column_count = int(column_count)
    except Exception:
        row_count = 100
        column_count = 26

    requests = [
        {
            "addSheet": {
                "properties": {
                    "title": title,
                    "gridProperties": {
                        "rowCount": row_count,
                        "columnCount": column_count,
                    },
                }
            }
        }
    ]
    try:
        result = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()
        replies = result.get("replies", []) or []
        added = replies[0].get("addSheet", {}).get("properties", {}) if replies else {}
        return {
            "status": "ok",
            "sheet_id": added.get("sheetId"),
            "title": added.get("title") or title,
        }
    except Exception as exc:  # pragma: no cover - external API
        logger.error("Sheets create_worksheet failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def append_values(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    range_ = params.get("range") or "Sheet1!A1"
    values = params.get("values") or []
    value_input_option = params.get("value_input_option") or "USER_ENTERED"

    try:
        resp = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption=value_input_option,
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        updates = resp.get("updates", {})
        return {
            "status": "ok",
            "updated_range": updates.get("updatedRange"),
            "updated_rows": updates.get("updatedRows"),
        }
    except Exception as exc:  # pragma: no cover - external API
        logger.error("Sheets append_values failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def get_values(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    range_ = params.get("range") or "Sheet1!A1"
    try:
        resp = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_,
        ).execute()
        return {"status": "ok", "range": resp.get("range"), "values": resp.get("values", [])}
    except Exception as exc:  # pragma: no cover - external API
        logger.error("Sheets get_values failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def update_cell(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    range_ = params.get("range") or params.get("cell") or "Sheet1!A1"
    value = params.get("value")
    value_input_option = params.get("value_input_option") or "USER_ENTERED"
    body_values: List[List[Any]] = [[value]]

    try:
        resp = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption=value_input_option,
            body={"values": body_values},
        ).execute()
        return {
            "status": "ok",
            "updated_range": resp.get("updatedRange"),
            "updated_cells": resp.get("updatedCells"),
        }
    except Exception as exc:  # pragma: no cover - external API
        logger.error("Sheets update_cell failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def describe_current_user(bound_bundle) -> Dict[str, Any]:
    credential = bound_bundle.credential
    return {
        "status": "ok",
        "email": credential.google_account_email,
        "scope_tier": bound_bundle.scope_tier,
        "scopes": credential.scopes_list(),
    }


def get_spreadsheet_info(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Get metadata about a spreadsheet including title and list of sheets."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    if not spreadsheet_id:
        return {"status": "error", "message": "spreadsheet_id is required"}

    try:
        resp = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="spreadsheetId,properties/title,sheets/properties",
        ).execute()

        sheets_list = []
        for sheet in resp.get("sheets", []):
            props = sheet.get("properties", {})
            sheets_list.append({
                "sheet_id": props.get("sheetId"),
                "title": props.get("title"),
                "index": props.get("index"),
                "row_count": props.get("gridProperties", {}).get("rowCount"),
                "column_count": props.get("gridProperties", {}).get("columnCount"),
            })

        return {
            "status": "ok",
            "spreadsheet_id": resp.get("spreadsheetId"),
            "title": resp.get("properties", {}).get("title"),
            "sheets": sheets_list,
            "url": SHEETS_URL_TEMPLATE.format(sheet_id=spreadsheet_id),
        }
    except Exception as exc:
        logger.error("Sheets get_spreadsheet_info failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def list_worksheets(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """List all worksheets/tabs in a spreadsheet."""
    # This is essentially the same as get_spreadsheet_info but returns just sheets
    result = get_spreadsheet_info(bound_bundle, params)
    if result.get("status") != "ok":
        return result
    return {
        "status": "ok",
        "spreadsheet_id": result.get("spreadsheet_id"),
        "sheets": result.get("sheets", []),
    }


def clear_range(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Clear values in a range (keeps formatting)."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    range_ = params.get("range")

    if not spreadsheet_id:
        return {"status": "error", "message": "spreadsheet_id is required"}
    if not range_:
        return {"status": "error", "message": "range is required"}

    try:
        resp = service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=range_,
        ).execute()
        return {
            "status": "ok",
            "cleared_range": resp.get("clearedRange"),
            "message": f"Cleared range {resp.get('clearedRange')}",
        }
    except Exception as exc:
        logger.error("Sheets clear_range failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def find_rows(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Find rows where a column matches a value."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    range_ = params.get("range") or "Sheet1"
    search_column = params.get("search_column", 0)  # 0-indexed column number or letter
    search_value = params.get("search_value")

    if not spreadsheet_id:
        return {"status": "error", "message": "spreadsheet_id is required"}
    if search_value is None:
        return {"status": "error", "message": "search_value is required"}

    # Convert column letter to index if needed
    if isinstance(search_column, str) and search_column.isalpha():
        search_column = sum((ord(c.upper()) - ord('A') + 1) * (26 ** i)
                           for i, c in enumerate(reversed(search_column))) - 1
    try:
        search_column = int(search_column)
    except (TypeError, ValueError):
        search_column = 0

    try:
        resp = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_,
        ).execute()

        values = resp.get("values", [])
        matching_rows = []

        for row_idx, row in enumerate(values):
            if search_column < len(row):
                cell_value = row[search_column]
                # Case-insensitive string comparison
                if str(cell_value).lower() == str(search_value).lower():
                    matching_rows.append({
                        "row_index": row_idx,
                        "row_number": row_idx + 1,  # 1-indexed for user
                        "values": row,
                    })

        return {
            "status": "ok",
            "matches_found": len(matching_rows),
            "rows": matching_rows,
        }
    except Exception as exc:
        logger.error("Sheets find_rows failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def delete_rows(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Delete rows from a sheet."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    sheet_id = params.get("sheet_id")  # Numeric sheet ID (not name)
    start_row = params.get("start_row")  # 0-indexed
    end_row = params.get("end_row")  # 0-indexed, exclusive

    if not spreadsheet_id:
        return {"status": "error", "message": "spreadsheet_id is required"}
    if sheet_id is None:
        return {"status": "error", "message": "sheet_id is required (numeric ID, not sheet name)"}
    if start_row is None:
        return {"status": "error", "message": "start_row is required (0-indexed)"}

    try:
        start_row = int(start_row)
        end_row = int(end_row) if end_row is not None else start_row + 1
        sheet_id = int(sheet_id)
    except (TypeError, ValueError):
        return {"status": "error", "message": "start_row, end_row, and sheet_id must be integers"}

    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": start_row,
                    "endIndex": end_row,
                }
            }
        }
    ]

    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()

        rows_deleted = end_row - start_row
        return {
            "status": "ok",
            "message": f"Deleted {rows_deleted} row(s) starting at row {start_row + 1}",
            "rows_deleted": rows_deleted,
        }
    except Exception as exc:
        logger.error("Sheets delete_rows failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def update_values(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Update multiple cells in a range at once."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_sheets_service(credentials)
    if err:
        return err

    spreadsheet_id = params.get("spreadsheet_id")
    range_ = params.get("range")
    values = params.get("values")  # 2D array
    value_input_option = params.get("value_input_option") or "USER_ENTERED"

    if not spreadsheet_id:
        return {"status": "error", "message": "spreadsheet_id is required"}
    if not range_:
        return {"status": "error", "message": "range is required"}
    if not values:
        return {"status": "error", "message": "values is required"}

    try:
        resp = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption=value_input_option,
            body={"values": values},
        ).execute()
        return {
            "status": "ok",
            "updated_range": resp.get("updatedRange"),
            "updated_rows": resp.get("updatedRows"),
            "updated_columns": resp.get("updatedColumns"),
            "updated_cells": resp.get("updatedCells"),
        }
    except Exception as exc:
        logger.error("Sheets update_values failed: %s", exc)
        return {"status": "error", "message": str(exc)}
