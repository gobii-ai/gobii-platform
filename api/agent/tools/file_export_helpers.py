from collections.abc import Callable
from typing import Any, Dict, Tuple

from api.agent.files import attachment_helpers, filespace_service
from api.agent.tools.agent_variables import set_agent_variable
from api.agent.tools.attachment_guidance import build_attachment_result_message
from api.agent.tools.filespace_paths import normalize_filespace_tool_path
from api.services.system_settings import get_max_file_size


def resolve_export_target(
    params: Dict[str, Any],
    *,
    agent_id: Any = None,
) -> Tuple[str | None, bool, Dict[str, Any] | None]:
    if "filename" in params:
        return None, False, {"status": "error", "message": "Use file_path instead of filename."}
    if "path" in params:
        return None, False, {"status": "error", "message": "Use file_path instead of path."}

    file_path = params.get("file_path")
    if file_path is None:
        return None, False, {"status": "error", "message": "Missing required parameter: file_path"}
    if not isinstance(file_path, str):
        return None, False, {"status": "error", "message": "file_path must be a string"}
    raw_file_path = file_path
    file_path = normalize_filespace_tool_path(raw_file_path, agent_id=agent_id) or ""
    if not file_path and raw_file_path.strip():
        return None, False, {"status": "error", "message": "file_path is invalid"}
    if not file_path:
        return None, False, {"status": "error", "message": "file_path must be a non-empty string"}

    overwrite = params.get("overwrite")
    if overwrite is None:
        overwrite_flag = False
    elif isinstance(overwrite, bool):
        overwrite_flag = overwrite
    else:
        return None, False, {"status": "error", "message": "overwrite must be a boolean when provided"}

    return file_path, overwrite_flag, None


def write_agent_export(
    *,
    agent,
    content_bytes: bytes,
    extension: str,
    mime_type: str,
    path: str,
    overwrite: bool,
    size_label: str,
    include_message: bool = False,
    inline: Callable[[str, str], str] | None = None,
    inline_html: Callable[[str, str], str] | None = None,
    extra: Dict[str, Any] | None = None,
    write_bytes_func: Callable[..., Dict[str, Any]] | None = None,
    signed_url_func: Callable[..., str] | None = None,
    set_variable_func: Callable[[str, str], None] | None = None,
) -> Dict[str, Any]:
    max_size = get_max_file_size()
    if max_size and len(content_bytes) > max_size:
        return {
            "status": "error",
            "message": f"{size_label} exceeds maximum allowed size ({len(content_bytes)} bytes > {max_size} bytes).",
        }

    result = (write_bytes_func or filespace_service.write_bytes_to_dir)(
        agent=agent,
        content_bytes=content_bytes,
        extension=extension,
        mime_type=mime_type,
        path=path,
        overwrite=overwrite,
    )
    if result.get("status") != "ok":
        return result

    file_path = result.get("path")
    signed_url = (signed_url_func or attachment_helpers.build_signed_filespace_download_url)(
        agent_id=str(agent.id),
        node_id=result.get("node_id"),
    )
    (set_variable_func or set_agent_variable)(file_path, signed_url)
    var_ref = f"$[{file_path}]"
    payload = {"status": "ok", "file": var_ref, "attach": var_ref}
    if include_message:
        payload["message"] = build_attachment_result_message(var_ref)
    if inline:
        payload["inline"] = inline(var_ref, signed_url)
    if inline_html:
        payload["inline_html"] = inline_html(var_ref, signed_url)
    if extra:
        payload.update(extra)
    return payload
