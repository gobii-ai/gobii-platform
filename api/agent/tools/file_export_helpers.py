from typing import Any, Dict, Tuple


def resolve_export_target(
    params: Dict[str, Any],
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
    file_path = file_path.strip()
    if file_path.startswith("$[") and file_path.endswith("]"):
        file_path = file_path[2:-1].strip()
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
