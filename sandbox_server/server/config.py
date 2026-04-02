import os
import re
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
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    env.setdefault("UV_PROJECT_ENVIRONMENT", _DEFAULT_UV_PROJECT_ENVIRONMENT)
    identity = agent_root.name if isinstance(agent_root, Path) and agent_root.name else "default"
    env.setdefault("UV_CACHE_DIR", str(_runtime_cache_root() / identity / "uv-cache"))
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
    base = _runtime_cache_root() / cleaned
    paths = {
        "base": base,
        "home": base / "home",
        "xdg": base / "xdg",
        "npm": base / "npm",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


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
