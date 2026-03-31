import json
import logging
from pathlib import Path
from typing import Any, Dict

from sandbox_server.config import _PROXY_ENV_KEYS

logger = logging.getLogger(__name__)


def _manifest_path(agent_root: Path) -> Path:
    meta_dir = agent_root / ".gobii"
    meta_dir.mkdir(parents=True, exist_ok=True)
    return meta_dir / "manifest.json"


def _load_manifest(agent_root: Path) -> Dict[str, Any]:
    path = _manifest_path(agent_root)
    if not path.exists():
        return {"files": {}, "deleted": {}}
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return {"files": {}, "deleted": {}}
    if not isinstance(data, dict):
        return {"files": {}, "deleted": {}}
    data.setdefault("files", {})
    data.setdefault("deleted", {})
    if not isinstance(data["files"], dict):
        data["files"] = {}
    if not isinstance(data["deleted"], dict):
        data["deleted"] = {}
    return data


def _save_manifest(agent_root: Path, manifest: Dict[str, Any]) -> None:
    path = _manifest_path(agent_root)
    path.write_text(json.dumps(manifest, sort_keys=True))


def _proxy_env_from_manifest(agent_root: Path) -> Dict[str, str]:
    manifest = _load_manifest(agent_root)
    env = manifest.get("env", {})
    if not isinstance(env, dict):
        return {}
    filtered: Dict[str, str] = {}
    for key in _PROXY_ENV_KEYS:
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            filtered[key] = value.strip()
    return filtered


def _store_proxy_env(agent_root: Path, payload: Dict[str, Any]) -> bool:
    proxy_env = payload.get("proxy_env")
    if not isinstance(proxy_env, dict):
        return False
    filtered: Dict[str, str] = {}
    for key in _PROXY_ENV_KEYS:
        value = proxy_env.get(key)
        if isinstance(value, str) and value.strip():
            filtered[key] = value.strip()
    if not filtered:
        return False
    manifest = _load_manifest(agent_root)
    current_env = manifest.get("env", {})
    if not isinstance(current_env, dict):
        current_env = {}
    next_env = {**current_env, **filtered}
    if next_env == current_env:
        return False
    changed_keys = sorted(key for key in filtered if current_env.get(key) != filtered[key])
    manifest["env"] = next_env
    _save_manifest(agent_root, manifest)
    logger.info(
        "Sandbox proxy env updated workspace=%s changed_keys=%s",
        str(agent_root),
        ",".join(changed_keys) if changed_keys else "none",
    )
    return True
