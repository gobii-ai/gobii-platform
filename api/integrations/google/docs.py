"""
Google Docs helpers for first-party tools.
"""

import logging
from typing import Any, Dict, List, Optional

from api.integrations.google.auth import ensure_fresh_credentials

logger = logging.getLogger(__name__)

DOCS_URL_TEMPLATE = "https://docs.google.com/document/d/{doc_id}/edit"


def _build_docs_service(credentials):
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None, {
            "status": "error",
            "message": (
                "google-api-python-client is not installed. "
                "Install it to enable Google Docs actions."
            ),
        }
    service = build("docs", "v1", credentials=credentials, cache_discovery=False)
    return service, None


def create_document(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_docs_service(credentials)
    if err:
        return err

    title = params.get("title") or "Untitled document"
    body_text = params.get("body")

    try:
        doc = service.documents().create(body={"title": title}).execute()
        document_id = doc.get("documentId")
        url = DOCS_URL_TEMPLATE.format(doc_id=document_id) if document_id else ""

        if body_text and document_id:
            requests: List[Dict[str, Any]] = [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": str(body_text),
                    }
                }
            ]
            try:
                service.documents().batchUpdate(
                    documentId=document_id,
                    body={"requests": requests},
                ).execute()
            except Exception:
                logger.warning("Failed to insert initial body text for doc %s", document_id, exc_info=True)

        return {
            "status": "ok",
            "document_id": document_id,
            "title": doc.get("title"),
            "url": url,
        }
    except Exception as exc:  # pragma: no cover - external API
        logger.error("Docs create_document failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def find_document(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err

    try:
        from googleapiclient.discovery import build
    except ImportError:
        return {
            "status": "error",
            "message": (
                "google-api-python-client is not installed. "
                "Install it to enable Google Docs search."
            ),
        }

    query = (params.get("query") or "").strip()
    limit = params.get("limit") or 5
    try:
        limit = int(limit)
    except Exception:
        limit = 5

    # Drive search for docs
    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    q_parts = ["mimeType='application/vnd.google-apps.document'", "trashed=false"]
    if query:
        safe_query = query.replace("'", "\\'")
        q_parts.append(f"name contains '{safe_query}'")
    q = " and ".join(q_parts)

    try:
        resp = drive_service.files().list(
            q=q,
            pageSize=limit,
            fields="files(id, name, owners/emailAddress, webViewLink)",
        ).execute()
        files = resp.get("files", []) or []
        results: List[Dict[str, Any]] = []
        for item in files:
            doc_id = item.get("id")
            results.append(
                {
                    "document_id": doc_id,
                    "name": item.get("name"),
                    "web_view_link": item.get("webViewLink") or (DOCS_URL_TEMPLATE.format(doc_id=doc_id) if doc_id else ""),
                    "owners": [owner.get("emailAddress") for owner in item.get("owners", []) if owner.get("emailAddress")],
                }
            )
        return {"status": "ok", "documents": results}
    except Exception as exc:  # pragma: no cover - external API
        logger.error("Docs find_document failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def describe_current_user(bound_bundle) -> Dict[str, Any]:
    credential = bound_bundle.credential
    email = credential.google_account_email or ""
    return {
        "status": "ok",
        "email": email,
        "scope_tier": bound_bundle.scope_tier,
        "scopes": credential.scopes_list(),
    }


def get_document_content(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Get the text content of a document."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_docs_service(credentials)
    if err:
        return err

    document_id = params.get("document_id")
    if not document_id:
        return {"status": "error", "message": "document_id is required"}

    try:
        doc = service.documents().get(documentId=document_id).execute()

        # Extract plain text from the document body
        content = doc.get("body", {}).get("content", [])
        text_parts: List[str] = []
        for element in content:
            if "paragraph" in element:
                for para_element in element["paragraph"].get("elements", []):
                    if "textRun" in para_element:
                        text_parts.append(para_element["textRun"].get("content", ""))
            elif "table" in element:
                text_parts.append("[TABLE]")

        return {
            "status": "ok",
            "document_id": document_id,
            "title": doc.get("title"),
            "text": "".join(text_parts),
            "url": DOCS_URL_TEMPLATE.format(doc_id=document_id),
        }
    except Exception as exc:
        logger.error("Docs get_document_content failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def append_text(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Append text to the end of a document."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_docs_service(credentials)
    if err:
        return err

    document_id = params.get("document_id")
    text = params.get("text")
    if not document_id:
        return {"status": "error", "message": "document_id is required"}
    if not text:
        return {"status": "error", "message": "text is required"}

    try:
        # Get document to find the end index
        doc = service.documents().get(documentId=document_id).execute()
        end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)
        # Insert before the final newline
        insert_index = max(1, end_index - 1)

        requests = [{"insertText": {"location": {"index": insert_index}, "text": str(text)}}]
        service.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute()

        return {
            "status": "ok",
            "document_id": document_id,
            "message": "Text appended successfully",
            "url": DOCS_URL_TEMPLATE.format(doc_id=document_id),
        }
    except Exception as exc:
        logger.error("Docs append_text failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def replace_all_text(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Find and replace all occurrences of text in a document."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_docs_service(credentials)
    if err:
        return err

    document_id = params.get("document_id")
    find_text = params.get("find_text")
    replace_text = params.get("replace_text", "")
    match_case = params.get("match_case", True)

    if not document_id:
        return {"status": "error", "message": "document_id is required"}
    if not find_text:
        return {"status": "error", "message": "find_text is required"}

    try:
        requests = [
            {
                "replaceAllText": {
                    "containsText": {"text": str(find_text), "matchCase": bool(match_case)},
                    "replaceText": str(replace_text),
                }
            }
        ]
        result = service.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute()

        # Get count of replacements made
        replies = result.get("replies", [])
        occurrences_changed = 0
        if replies and "replaceAllText" in replies[0]:
            occurrences_changed = replies[0]["replaceAllText"].get("occurrencesChanged", 0)

        return {
            "status": "ok",
            "document_id": document_id,
            "occurrences_changed": occurrences_changed,
            "message": f"Replaced {occurrences_changed} occurrence(s) of '{find_text}'",
            "url": DOCS_URL_TEMPLATE.format(doc_id=document_id),
        }
    except Exception as exc:
        logger.error("Docs replace_all_text failed: %s", exc)
        return {"status": "error", "message": str(exc)}


def insert_table(bound_bundle, params: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a table into a document."""
    credentials, err = ensure_fresh_credentials(bound_bundle)
    if err:
        return err
    service, err = _build_docs_service(credentials)
    if err:
        return err

    document_id = params.get("document_id")
    rows = params.get("rows", 3)
    columns = params.get("columns", 3)
    data = params.get("data")  # Optional 2D list of cell values

    if not document_id:
        return {"status": "error", "message": "document_id is required"}

    try:
        rows = int(rows)
        columns = int(columns)
    except (TypeError, ValueError):
        return {"status": "error", "message": "rows and columns must be integers"}

    try:
        # Get document to find insert location (end of doc)
        doc = service.documents().get(documentId=document_id).execute()
        end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)
        insert_index = max(1, end_index - 1)

        # Insert the table
        requests: List[Dict[str, Any]] = [
            {
                "insertTable": {
                    "location": {"index": insert_index},
                    "rows": rows,
                    "columns": columns,
                }
            }
        ]
        service.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute()

        # If data provided, populate cells
        if data and isinstance(data, list):
            # Re-fetch doc to get table cell indexes
            doc = service.documents().get(documentId=document_id).execute()

            # Find the table we just inserted
            table_element = None
            for element in doc.get("body", {}).get("content", []):
                if "table" in element:
                    table_element = element["table"]

            if table_element:
                cell_requests: List[Dict[str, Any]] = []
                table_rows = table_element.get("tableRows", [])
                for row_idx, row_data in enumerate(data):
                    if row_idx >= len(table_rows):
                        break
                    row = table_rows[row_idx]
                    cells = row.get("tableCells", [])
                    if not isinstance(row_data, list):
                        row_data = [row_data]
                    for col_idx, cell_value in enumerate(row_data):
                        if col_idx >= len(cells):
                            break
                        cell = cells[col_idx]
                        # Get the start index of the cell content
                        cell_content = cell.get("content", [])
                        if cell_content:
                            cell_start = cell_content[0].get("startIndex", 0)
                            if cell_start > 0:
                                cell_requests.append({
                                    "insertText": {
                                        "location": {"index": cell_start},
                                        "text": str(cell_value),
                                    }
                                })

                if cell_requests:
                    # Insert in reverse order to maintain correct indexes
                    cell_requests.reverse()
                    service.documents().batchUpdate(documentId=document_id, body={"requests": cell_requests}).execute()

        return {
            "status": "ok",
            "document_id": document_id,
            "message": f"Inserted {rows}x{columns} table",
            "url": DOCS_URL_TEMPLATE.format(doc_id=document_id),
        }
    except Exception as exc:
        logger.error("Docs insert_table failed: %s", exc)
        return {"status": "error", "message": str(exc)}
