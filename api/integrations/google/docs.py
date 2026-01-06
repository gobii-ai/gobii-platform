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
