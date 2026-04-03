import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from api.services.sandbox_internal_paths import CUSTOM_TOOL_SQLITE_FILESPACE_PATH
from sandbox_server.config import _agent_workspace, _workspace_max_bytes
from sandbox_server.manifest import _load_manifest, _proxy_env_from_manifest, _save_manifest, _store_proxy_env
from sandbox_server.workspace import (
    _checksum_bytes,
    _decode_content,
    _elapsed_ms,
    _ensure_capacity,
    _guess_mime_type,
    _iter_workspace_files,
    _normalize_checksum,
    _normalize_workspace_path,
    _parse_entry_updated_at,
    _parse_since,
    _require_agent_id,
    _resolve_local_checksum,
    _safe_url_for_log,
    _trace_context,
    _workspace_size_bytes,
)

logger = logging.getLogger(__name__)
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _requests_proxies_from_env(proxy_env: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if not proxy_env:
        return None
    http_proxy = proxy_env.get("http_proxy") or proxy_env.get("HTTP_PROXY")
    https_proxy = proxy_env.get("https_proxy") or proxy_env.get("HTTPS_PROXY") or http_proxy
    all_proxy = proxy_env.get("all_proxy") or proxy_env.get("ALL_PROXY")
    no_proxy = proxy_env.get("no_proxy") or proxy_env.get("NO_PROXY")
    proxies: Dict[str, str] = {}
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    if all_proxy:
        proxies["all"] = all_proxy
    if no_proxy:
        proxies["no_proxy"] = no_proxy
    return proxies or None


def _download_file(url: str, expected_size: Optional[int], proxy_env: Optional[Dict[str, str]] = None) -> bytes:
    started_at = time.monotonic()
    safe_url = _safe_url_for_log(url)
    max_bytes = _workspace_max_bytes()
    if expected_size and max_bytes > 0 and expected_size > max_bytes:
        raise RuntimeError("Download exceeds workspace size limit.")
    try:
        request_kwargs: Dict[str, Any] = {"stream": True, "timeout": 30}
        proxies = _requests_proxies_from_env(proxy_env)
        if proxies:
            request_kwargs["proxies"] = proxies
        with requests.get(url, **request_kwargs) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                data.extend(chunk)
                if max_bytes > 0 and len(data) > max_bytes:
                    raise RuntimeError("Downloaded file exceeds workspace size limit.")
            result = bytes(data)
            logger.info(
                "Sandbox download_file url=%s status=ok bytes=%s duration_ms=%s",
                safe_url,
                len(result),
                _elapsed_ms(started_at),
            )
            return result
    except requests.RequestException as exc:
        logger.exception(
            "Sandbox download_file url=%s status=error duration_ms=%s",
            safe_url,
            _elapsed_ms(started_at),
        )
        raise RuntimeError(f"Failed to download file: {exc}") from exc


def _clear_sqlite_sidecars(path) -> None:
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        try:
            path.with_name(path.name + suffix).unlink()
        except FileNotFoundError:
            continue


def _requested_push_paths(agent_root, payload: Dict[str, Any]) -> list[tuple[Any, str]]:
    requested = payload.get("internal_paths") or []
    if not isinstance(requested, list):
        return []

    normalized_paths: list[tuple[Any, str]] = []
    seen_paths: set[str] = set()
    for requested_path in requested:
        full_path, normalized = _normalize_workspace_path(agent_root, requested_path)
        if full_path is None or not normalized or normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        normalized_paths.append((full_path, normalized))
    return normalized_paths


def _handle_sync_filespace(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = time.monotonic()
    agent_id, error = _require_agent_id(payload)
    if error:
        return error
    trace_id, _traceparent = _trace_context(payload)
    direction = payload.get("direction")
    if not isinstance(direction, str) or not direction.strip():
        return {"status": "error", "message": "Missing required parameter: direction"}
    direction = direction.strip().lower()
    agent_root = _agent_workspace(agent_id)
    proxy_env_updated = _store_proxy_env(agent_root, payload)
    proxy_env = _proxy_env_from_manifest(agent_root)
    manifest_load_started = time.monotonic()
    manifest = _load_manifest(agent_root)
    manifest_load_ms = _elapsed_ms(manifest_load_started)

    if direction == "push":
        since = _parse_since(payload.get("since"))
        scan_started_at = time.monotonic()
        changes: list[Dict[str, Any]] = []
        seen_paths: set[str] = set()
        scanned_files = 0
        uploaded_files = 0
        uploaded_bytes = 0
        deleted_count = 0
        for path in _iter_workspace_files(agent_root):
            scanned_files += 1
            rel = "/" + path.relative_to(agent_root).as_posix()
            seen_paths.add(rel)
            try:
                stat = path.stat()
            except OSError:
                continue
            mtime = stat.st_mtime
            if since is not None and mtime <= since:
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            mime_type = _guess_mime_type(path)
            checksum_sha256 = _checksum_bytes(content)
            changes.append(
                {
                    "path": rel,
                    "content_b64": base64.b64encode(content).decode("utf-8"),
                    "mime_type": mime_type,
                    "checksum_sha256": checksum_sha256,
                }
            )
            manifest["files"][rel] = {
                "mtime": mtime,
                "size": stat.st_size,
                "checksum_sha256": checksum_sha256,
            }
            manifest["deleted"].pop(rel, None)
            uploaded_files += 1
            uploaded_bytes += len(content)

        for path, rel in _requested_push_paths(agent_root, payload):
            if rel in seen_paths:
                continue
            seen_paths.add(rel)
            try:
                stat = path.stat()
            except OSError:
                changes.append({"path": rel, "is_deleted": True})
                deleted_count += 1
                continue
            if not path.is_file():
                continue
            mtime = stat.st_mtime
            if since is not None and mtime <= since:
                continue
            try:
                content = path.read_bytes()
            except OSError:
                continue
            mime_type = _guess_mime_type(path)
            changes.append(
                {
                    "path": rel,
                    "content_b64": base64.b64encode(content).decode("utf-8"),
                    "mime_type": mime_type,
                    "checksum_sha256": _checksum_bytes(content),
                }
            )
            uploaded_files += 1
            uploaded_bytes += len(content)

        deleted = manifest.get("deleted", {})
        tracked = set(manifest.get("files", {}).keys())
        for rel in sorted(tracked - seen_paths):
            deleted_at = time.time()
            if since is not None and deleted_at <= since:
                continue
            changes.append({"path": rel, "is_deleted": True})
            deleted[rel] = {"deleted_at": deleted_at}
            manifest["files"].pop(rel, None)
            deleted_count += 1
        manifest["deleted"] = deleted
        _save_manifest(agent_root, manifest)
        response = {
            "status": "ok",
            "changes": changes,
            "sync_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            (
                "Sandbox sync_filespace agent=%s direction=push status=ok "
                "changes=%s uploaded_files=%s uploaded_bytes=%s deleted=%s scanned_files=%s "
                "scan_ms=%s manifest_load_ms=%s total_ms=%s since_set=%s proxy_env_updated=%s trace_id=%s"
            ),
            agent_id,
            len(changes),
            uploaded_files,
            uploaded_bytes,
            deleted_count,
            scanned_files,
            _elapsed_ms(scan_started_at),
            manifest_load_ms,
            _elapsed_ms(started_at),
            since is not None,
            proxy_env_updated,
            trace_id,
        )
        return response

    if direction == "pull":
        entries = payload.get("files") or payload.get("changes") or []
        if not isinstance(entries, list):
            return {"status": "error", "message": "files must be a list."}
        pull_started_at = time.monotonic()
        workspace_bytes = _workspace_size_bytes(agent_root)
        skipped = 0
        conflicts = 0
        downloaded_files = 0
        downloaded_bytes = 0
        download_total_ms = 0
        inline_content_files = 0
        deleted_entries = 0
        checksum_skips = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            full_path, normalized = _normalize_workspace_path(agent_root, path)
            if full_path is None or not normalized:
                continue
            is_custom_tool_sqlite = normalized == CUSTOM_TOOL_SQLITE_FILESPACE_PATH
            entry_updated_at = _parse_entry_updated_at(entry)
            is_deleted = bool(entry.get("is_deleted"))
            if is_deleted:
                if is_custom_tool_sqlite:
                    _clear_sqlite_sidecars(full_path)
                deleted_entries += 1
                existing_bytes = 0
                local_mtime = None
                if full_path.exists():
                    try:
                        local_stat = full_path.stat()
                        existing_bytes = local_stat.st_size
                        local_mtime = local_stat.st_mtime
                    except OSError:
                        local_mtime = None
                if entry_updated_at is not None and full_path.exists():
                    if local_mtime is not None and local_mtime > entry_updated_at:
                        conflicts += 1
                        skipped += 1
                        continue
                try:
                    if full_path.exists():
                        full_path.unlink()
                        workspace_bytes = max(workspace_bytes - existing_bytes, 0)
                except OSError:
                    continue
                manifest["files"].pop(normalized, None)
                deleted_at = entry_updated_at if entry_updated_at is not None else time.time()
                manifest.setdefault("deleted", {})[normalized] = {"deleted_at": deleted_at}
                continue

            remote_checksum = _normalize_checksum(entry.get("checksum_sha256"))
            local_meta = manifest.get("files", {}).get(normalized)
            if is_custom_tool_sqlite:
                _clear_sqlite_sidecars(full_path)
            if remote_checksum and full_path.exists():
                local_checksum = _resolve_local_checksum(full_path, local_meta)
                if local_checksum == remote_checksum:
                    try:
                        local_stat = full_path.stat()
                    except OSError:
                        local_stat = None
                    manifest["files"][normalized] = {
                        "mtime": local_stat.st_mtime if local_stat else time.time(),
                        "size": local_stat.st_size if local_stat else 0,
                        "updated_at": entry.get("updated_at"),
                        "checksum_sha256": remote_checksum,
                    }
                    manifest["deleted"].pop(normalized, None)
                    skipped += 1
                    checksum_skips += 1
                    continue

            content = _decode_content(entry)
            if content is None and entry.get("download_url"):
                download_started_at = time.monotonic()
                try:
                    content = _download_file(
                        entry["download_url"],
                        entry.get("size_bytes"),
                        proxy_env,
                    )
                except RuntimeError as exc:
                    message = str(exc)
                    logger.warning(
                        (
                            "Sandbox sync_filespace agent=%s direction=pull status=error "
                            "message=%s entries=%s skipped=%s conflicts=%s download_ms=%s "
                            "manifest_load_ms=%s total_ms=%s proxy_env_updated=%s trace_id=%s"
                        ),
                        agent_id,
                        message,
                        len(entries),
                        skipped,
                        conflicts,
                        download_total_ms,
                        manifest_load_ms,
                        _elapsed_ms(started_at),
                        proxy_env_updated,
                        trace_id,
                    )
                    return {"status": "error", "message": message}
                downloaded_files += 1
                downloaded_bytes += len(content)
                download_total_ms += _elapsed_ms(download_started_at)
            elif content is not None:
                inline_content_files += 1
            if content is None:
                message = f"Missing content for {normalized}"
                logger.warning(
                    (
                        "Sandbox sync_filespace agent=%s direction=pull status=error "
                        "message=%s entries=%s skipped=%s conflicts=%s downloaded_files=%s "
                        "manifest_load_ms=%s total_ms=%s proxy_env_updated=%s trace_id=%s"
                    ),
                    agent_id,
                    message,
                    len(entries),
                    skipped,
                    conflicts,
                    downloaded_files,
                    manifest_load_ms,
                    _elapsed_ms(started_at),
                    proxy_env_updated,
                    trace_id,
                )
                return {"status": "error", "message": message}

            existing_bytes = 0
            local_mtime = None
            if full_path.exists():
                try:
                    local_stat = full_path.stat()
                    existing_bytes = local_stat.st_size
                    local_mtime = local_stat.st_mtime
                except OSError:
                    local_mtime = None
            if entry_updated_at is not None and full_path.exists():
                if local_mtime is not None and local_mtime > entry_updated_at:
                    conflicts += 1
                    skipped += 1
                    continue

            capacity_error = _ensure_capacity(
                agent_root,
                len(content),
                existing_bytes=existing_bytes,
                current_bytes=workspace_bytes,
            )
            if capacity_error:
                logger.warning(
                    (
                        "Sandbox sync_filespace agent=%s direction=pull status=error "
                        "message=%s entries=%s skipped=%s conflicts=%s downloaded_files=%s "
                        "download_ms=%s manifest_load_ms=%s total_ms=%s proxy_env_updated=%s trace_id=%s"
                    ),
                    agent_id,
                    capacity_error.get("message"),
                    len(entries),
                    skipped,
                    conflicts,
                    downloaded_files,
                    download_total_ms,
                    manifest_load_ms,
                    _elapsed_ms(started_at),
                    proxy_env_updated,
                    trace_id,
                )
                return capacity_error

            full_path.parent.mkdir(parents=True, exist_ok=True)
            if is_custom_tool_sqlite:
                _clear_sqlite_sidecars(full_path)
            try:
                with open(full_path, "wb") as handle:
                    handle.write(content)
            except OSError as exc:
                logger.exception("Failed to write synced file agent=%s path=%s", agent_id, normalized)
                return {"status": "error", "message": f"Failed to write {normalized}: {exc}"}
            workspace_bytes = max(workspace_bytes - existing_bytes, 0) + len(content)
            written_checksum = remote_checksum or _checksum_bytes(content)
            manifest["files"][normalized] = {
                "mtime": full_path.stat().st_mtime,
                "size": full_path.stat().st_size,
                "updated_at": entry.get("updated_at"),
                "checksum_sha256": written_checksum,
            }
            manifest["deleted"].pop(normalized, None)

        _save_manifest(agent_root, manifest)
        response = {
            "status": "ok",
            "applied": len(entries) - skipped,
            "skipped": skipped,
            "conflicts": conflicts,
            "sync_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            (
                "Sandbox sync_filespace agent=%s direction=pull status=ok "
                "entries=%s applied=%s skipped=%s conflicts=%s deleted_entries=%s "
                "checksum_skips=%s inline_files=%s downloaded_files=%s downloaded_bytes=%s download_ms=%s "
                "pull_ms=%s manifest_load_ms=%s total_ms=%s proxy_env_updated=%s trace_id=%s"
            ),
            agent_id,
            len(entries),
            response.get("applied"),
            skipped,
            conflicts,
            deleted_entries,
            checksum_skips,
            inline_content_files,
            downloaded_files,
            downloaded_bytes,
            download_total_ms,
            _elapsed_ms(pull_started_at),
            manifest_load_ms,
            _elapsed_ms(started_at),
            proxy_env_updated,
            trace_id,
        )
        return response

    message = "Invalid sync direction."
    logger.warning(
        "Sandbox sync_filespace agent=%s direction=%s status=error message=%s total_ms=%s trace_id=%s",
        agent_id,
        direction,
        message,
        _elapsed_ms(started_at),
        trace_id,
    )
    return {"status": "error", "message": message}
