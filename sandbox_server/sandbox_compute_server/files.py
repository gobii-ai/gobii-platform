import base64
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Optional

from sandbox_server.workspace import _ensure_capacity, _normalize_workspace_path

logger = logging.getLogger(__name__)


def _write_file(agent_root: Path, rel_path: str, content: bytes, overwrite: bool) -> Optional[Dict[str, Any]]:
    full_path, normalized = _normalize_workspace_path(agent_root, rel_path)
    if full_path is None:
        return {"status": "error", "message": "Invalid file_path."}
    if full_path.exists() and not overwrite:
        return {"status": "error", "message": "File already exists. Use overwrite=true to replace it."}

    existing_bytes = 0
    if full_path.exists():
        try:
            existing_bytes = full_path.stat().st_size
        except OSError:
            existing_bytes = 0

    capacity_error = _ensure_capacity(agent_root, len(content), existing_bytes=existing_bytes)
    if capacity_error:
        return capacity_error

    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(full_path, "wb") as handle:
            handle.write(content)
    except OSError as exc:
        logger.exception("Failed to write file to workspace path=%s", normalized or rel_path)
        return {"status": "error", "message": f"Failed to write file: {exc}"}
    return {"status": "ok", "export_path": normalized}


def _handle_create_file(agent_root: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return {"status": "error", "message": "Missing required parameter: content"}
    mime_type = payload.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type.strip():
        return {"status": "error", "message": "Missing required parameter: mime_type"}
    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return {"status": "error", "message": "Missing required parameter: file_path"}
    file_path = file_path.strip()
    overwrite = payload.get("overwrite") is True
    if not Path(file_path).suffix:
        base_type = mime_type.split(";", 1)[0].strip().lower()
        guessed = mimetypes.guess_extension(base_type or "") or ""
        if guessed:
            file_path = f"{file_path}{guessed}"
    response = _write_file(agent_root, file_path, content.encode("utf-8"), overwrite)
    if response.get("status") != "ok":
        return response
    response["mime_type"] = mime_type
    return response


def _blocked_html_assets(html: str) -> bool:
    lowered = html.lower()
    return "http://" in lowered or "https://" in lowered or "file://" in lowered


_TOKEN_PATTERN = re.compile(r"\$\[([^\]]+)\]")


def _substitute_workspace_tokens(html: str, agent_root: Path) -> str:
    def _replace(match: re.Match) -> str:
        token = match.group(1)
        full_path, _normalized = _normalize_workspace_path(agent_root, token)
        if full_path is None or not full_path.exists():
            return match.group(0)
        try:
            content = full_path.read_bytes()
        except OSError:
            return match.group(0)
        mime_type = mimetypes.guess_type(full_path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(content).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    return _TOKEN_PATTERN.sub(_replace, html)


def _handle_create_pdf(agent_root: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    html = payload.get("html")
    if not isinstance(html, str) or not html.strip():
        return {"status": "error", "message": "Missing required parameter: html"}
    file_path = payload.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return {"status": "error", "message": "Missing required parameter: file_path"}
    file_path = file_path.strip()
    if not Path(file_path).suffix:
        file_path = f"{file_path}.pdf"
    overwrite = payload.get("overwrite") is True

    html = _substitute_workspace_tokens(html, agent_root)

    if _blocked_html_assets(html):
        return {
            "status": "error",
            "message": "HTML contains blocked asset references (http/https/file).",
        }

    try:
        from weasyprint import HTML
    except Exception as exc:
        return {"status": "error", "message": f"PDF renderer unavailable: {exc}"}

    try:
        pdf_bytes = HTML(string=html, base_url=str(agent_root)).write_pdf()
    except Exception as exc:
        return {"status": "error", "message": f"Failed to render PDF: {exc}"}

    response = _write_file(agent_root, file_path, pdf_bytes, overwrite)
    if response.get("status") != "ok":
        return response
    response["mime_type"] = "application/pdf"
    return response
