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
