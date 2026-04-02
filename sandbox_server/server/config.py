import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Optional, Sequence

_DEFAULT_ALLOWED_ENV_KEYS = {
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TERM",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "PYTHONUNBUFFERED",
    "PYTHONIOENCODING",
    "UV_CACHE_DIR",
    "UV_PROJECT_ENVIRONMENT",
    "UV_TOOL_DIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "NPM_CONFIG_CACHE",
    "npm_config_cache",
    "PIP_CACHE_DIR",
}

_PROXY_ENV_KEYS = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "FTP_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "ftp_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
}
_TRACEPARENT_HEADER = "HTTP_TRACEPARENT"
_TRACE_ID_HEX_LEN = 32
_TRACEPARENT_PARTS = 4
_DEFAULT_UV_PROJECT_ENVIRONMENT = ".gobii/uv-project-env"


def _safe_identity_segment(value: str, *, fallback: str = "default") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", (value or "").strip())
    return cleaned or fallback


def _should_replace_runtime_path(path_value: str, *, generic_defaults: Sequence[str] = ()) -> bool:
    cleaned = (path_value or "").strip()
    if not cleaned:
        return True
    if cleaned in generic_defaults:
        return True
    try:
        candidate = Path(cleaned).expanduser().resolve()
        workspace = _workspace_root().resolve()
    except OSError:
        return False
    return candidate == workspace or workspace in candidate.parents


def _allowed_env_keys() -> set[str]:
    raw = os.environ.get("SANDBOX_COMPUTE_ALLOWED_ENV_KEYS", "")
    if not raw.strip():
        return set(_DEFAULT_ALLOWED_ENV_KEYS)
    parts = [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]
    return set(parts) or set(_DEFAULT_ALLOWED_ENV_KEYS)


def _sandbox_env(
    agent_root: Optional[Path] = None,
    extra_env: Optional[Dict[str, str]] = None,
    trusted_env_keys: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    allowed = _allowed_env_keys()
    trusted = {str(key) for key in (trusted_env_keys or []) if isinstance(key, str) and key.strip()}
    identity = agent_root.name if isinstance(agent_root, Path) and agent_root.name else "default"
    runtime_paths = _runtime_cache_paths(identity)
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if _should_replace_runtime_path(env.get("HOME", ""), generic_defaults=("/tmp",)):
        env["HOME"] = str(runtime_paths["home"])
    if _should_replace_runtime_path(env.get("TMPDIR", ""), generic_defaults=("/tmp",)):
        env["TMPDIR"] = str(runtime_paths["tmp"])

    uv_project_environment = (
        str(agent_root / _DEFAULT_UV_PROJECT_ENVIRONMENT)
        if isinstance(agent_root, Path)
        else _DEFAULT_UV_PROJECT_ENVIRONMENT
    )
    if _should_replace_runtime_path(
        env.get("UV_PROJECT_ENVIRONMENT", ""),
        generic_defaults=(".venv", "/app/.venv"),
    ):
        env["UV_PROJECT_ENVIRONMENT"] = uv_project_environment
    if _should_replace_runtime_path(env.get("UV_CACHE_DIR", ""), generic_defaults=("/tmp/.uv-cache",)):
        env["UV_CACHE_DIR"] = str(runtime_paths["uv_cache"])
    if _should_replace_runtime_path(env.get("UV_TOOL_DIR", ""), generic_defaults=("/tmp/.uv-tools",)):
        env["UV_TOOL_DIR"] = str(runtime_paths["uv_tools"])
    if _should_replace_runtime_path(env.get("XDG_CACHE_HOME", ""), generic_defaults=("/tmp/.cache",)):
        env["XDG_CACHE_HOME"] = str(runtime_paths["xdg_cache"])
    if _should_replace_runtime_path(env.get("XDG_CONFIG_HOME", "")):
        env["XDG_CONFIG_HOME"] = str(runtime_paths["xdg_config"])
    if _should_replace_runtime_path(env.get("XDG_DATA_HOME", "")):
        env["XDG_DATA_HOME"] = str(runtime_paths["xdg_data"])

    npm_cache = (env.get("NPM_CONFIG_CACHE") or env.get("npm_config_cache") or "").strip()
    if _should_replace_runtime_path(npm_cache, generic_defaults=("/tmp/.npm",)):
        npm_cache = str(runtime_paths["npm"])
    env["NPM_CONFIG_CACHE"] = npm_cache
    env["npm_config_cache"] = npm_cache

    if _should_replace_runtime_path(env.get("PIP_CACHE_DIR", ""), generic_defaults=("/tmp/.cache/pip",)):
        env["PIP_CACHE_DIR"] = str(runtime_paths["pip"])
    if extra_env:
        for key, value in extra_env.items():
            if key in allowed or key.startswith("SANDBOX_") or key in trusted:
                env[key] = str(value)
    return env


def _workspace_root() -> Path:
    root = os.environ.get("SANDBOX_WORKSPACE_ROOT", "/workspace").strip() or "/workspace"
    return Path(root)


def _runtime_cache_root() -> Path:
    root = os.environ.get("SANDBOX_RUNTIME_CACHE_ROOT", "/runtime-cache").strip() or "/runtime-cache"
    return Path(root)


def _runtime_cache_paths(identity: str) -> Dict[str, Path]:
    cleaned = _safe_identity_segment(identity)
    configured_root = os.environ.get("SANDBOX_RUNTIME_CACHE_ROOT", "/runtime-cache").strip() or "/runtime-cache"
    candidate_roots = [Path(configured_root)]
    if configured_root == "/runtime-cache":
        candidate_roots.append(Path(tempfile.gettempdir()) / "gobii-runtime-cache")

    last_error: Optional[OSError] = None
    for root in candidate_roots:
        base = root / cleaned
        paths = {
            "base": base,
            "home": base / "home",
            "tmp": base / "tmp",
            "uv_cache": base / "uv-cache",
            "uv_tools": base / "uv-tools",
            "xdg": base / "xdg",
            "xdg_cache": base / "xdg-cache",
            "xdg_config": base / "xdg-config",
            "xdg_data": base / "xdg-data",
            "npm": base / "npm",
            "pip": base / "pip",
        }
        try:
            for path in set(paths.values()):
                path.mkdir(parents=True, exist_ok=True)
            return paths
        except OSError as exc:
            last_error = exc

    assert last_error is not None
    raise last_error


def _workspace_max_bytes() -> int:
    raw = os.environ.get("SANDBOX_WORKSPACE_MAX_BYTES") or os.environ.get("SANDBOX_COMPUTE_WORKSPACE_LIMIT_BYTES")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 1024 * 1024 * 1024


def _stdio_max_bytes() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_STDIO_MAX_BYTES")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 1024 * 1024


def _run_command_timeout_seconds() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_RUN_COMMAND_TIMEOUT_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 120


def _python_default_timeout_seconds() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_PYTHON_DEFAULT_TIMEOUT_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 30


def _python_max_timeout_seconds() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_PYTHON_MAX_TIMEOUT_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 120


def _agent_workspace(agent_id: str) -> Path:
    root = _workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / _safe_identity_segment(agent_id, fallback="agent")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _mcp_timeout_seconds() -> int:
    raw = os.environ.get("SANDBOX_COMPUTE_MCP_TIMEOUT_SECONDS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 120
