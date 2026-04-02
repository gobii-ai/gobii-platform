import ast
import base64
import contextlib
import json
import logging
import os
import posixpath
import re
import tempfile
import textwrap
from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import signing
from django.core.files.storage import default_storage
from django.urls import reverse

from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.models import (
    AgentFileSpaceAccess,
    AgentFsNode,
    PersistentAgent,
    PersistentAgentCustomTool,
    PersistentAgentEnabledTool,
)
from api.agent.tools.sqlite_state import agent_sqlite_db, get_sqlite_db_path
from api.agent.tools.runtime_execution_context import get_tool_execution_context
from api.services.sandbox_compute import (
    LocalSandboxBackend,
    SandboxComputeService,
    SandboxComputeUnavailable,
    sandbox_compute_enabled_for_agent,
)
from api.services.system_settings import get_max_file_size

logger = logging.getLogger(__name__)

CUSTOM_TOOL_PREFIX = "custom_"
CUSTOM_TOOL_BRIDGE_SALT = "persistent-agent-custom-tool-bridge"
CUSTOM_TOOL_BRIDGE_TTL_SECONDS = 1200
CUSTOM_TOOL_RESULT_MARKER = "__GOBII_CUSTOM_TOOL_RESULT__="
DEFAULT_CUSTOM_TOOL_TIMEOUT_SECONDS = 300
MAX_CUSTOM_TOOL_TIMEOUT_SECONDS = 900
MAX_CUSTOM_TOOL_SOURCE_BYTES = 64 * 1024

_PARAMS_ENV_KEY = "SANDBOX_CUSTOM_TOOL_PARAMS_B64"
_BRIDGE_URL_ENV_KEY = "SANDBOX_CUSTOM_TOOL_BRIDGE_URL"
_TOKEN_ENV_KEY = "SANDBOX_CUSTOM_TOOL_TOKEN"
_TOOL_NAME_ENV_KEY = "SANDBOX_CUSTOM_TOOL_NAME"
_SOURCE_PATH_ENV_KEY = "SANDBOX_CUSTOM_TOOL_SOURCE_PATH"
_EXEC_SOURCE_PATH_ENV_KEY = "SANDBOX_CUSTOM_TOOL_EXEC_SOURCE_PATH"
_UV_CACHE_DIR_ENV_KEY = "SANDBOX_CUSTOM_TOOL_UV_CACHE_DIR"
_UV_INSTALL_DIR_ENV_KEY = "SANDBOX_CUSTOM_TOOL_UV_INSTALL_DIR"
_SQLITE_DB_PATH_ENV_KEY = "SANDBOX_CUSTOM_TOOL_SQLITE_DB_PATH"
_RUNTIME_CACHE_ROOT_ENV_KEY = "SANDBOX_RUNTIME_CACHE_ROOT"

_GOBII_CTX_MODULE = textwrap.dedent(
    f"""\
    import base64
    import json
    import os
    import subprocess
    import sys

    RESULT_MARKER = {CUSTOM_TOOL_RESULT_MARKER!r}
    CURL_STATUS_MARKER = "__GOBII_CURL_STATUS__:"

    def _decode_json_env(key, default):
        raw = os.environ.get(key, "")
        if not raw:
            return default
        return json.loads(base64.b64decode(raw.encode("utf-8")).decode("utf-8"))

    class ToolContext:
        def __init__(self):
            self.tool_name = os.environ.get({_TOOL_NAME_ENV_KEY!r}, "")
            self.source_path = os.environ.get({_SOURCE_PATH_ENV_KEY!r}, "")
            self.bridge_url = os.environ.get({_BRIDGE_URL_ENV_KEY!r}, "")
            self.token = os.environ.get({_TOKEN_ENV_KEY!r}, "")
            self.sqlite_db_path = os.environ.get({_SQLITE_DB_PATH_ENV_KEY!r}, "")

        def _call_tool_via_curl(self, body):
            command = [
                "curl",
                "-sS",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
                "-H",
                f"Authorization: Bearer {{self.token}}",
                "--data-binary",
                "@-",
                "-w",
                "\\n" + CURL_STATUS_MARKER + "%{{http_code}}",
                self.bridge_url,
            ]
            try:
                completed = subprocess.run(
                    command,
                    input=body,
                    capture_output=True,
                    timeout=300,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("curl is required for ctx.call_tool().") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Tool bridge request timed out.") from exc

            if completed.returncode != 0:
                stderr = completed.stderr.decode("utf-8", "replace")
                raise RuntimeError(f"Tool bridge request failed via curl: {{stderr[:500]}}")

            stdout = completed.stdout.decode("utf-8", "replace")
            marker = f"\\n{{CURL_STATUS_MARKER}}"
            marker_index = stdout.rfind(marker)
            if marker_index == -1:
                marker = CURL_STATUS_MARKER
                marker_index = stdout.rfind(marker)
            if marker_index == -1:
                raise RuntimeError(f"Tool bridge curl response missing status marker: {{stdout[:500]}}")

            raw = stdout[:marker_index]
            status_text = stdout[marker_index + len(marker):].strip()
            try:
                status_code = int(status_text)
            except ValueError as exc:
                raise RuntimeError(f"Tool bridge curl response had invalid status: {{status_text[:100]}}") from exc
            if status_code >= 400:
                raise RuntimeError(f"Tool bridge returned HTTP {{status_code}}: {{raw[:500]}}")
            return raw

        def call_tool(self, tool_name, params=None, **kwargs):
            payload = {{
                "tool_name": tool_name,
                "params": params if params is not None else kwargs,
            }}
            body = json.dumps(payload).encode("utf-8")
            raw = self._call_tool_via_curl(body)
            try:
                return json.loads(raw or "{{}}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Tool bridge returned invalid JSON: {{raw[:500]}}") from exc

        def log(self, *parts):
            print(*parts, file=sys.stderr)

    def _json_safe(value):
        return json.loads(json.dumps(value, default=str))

    def main(run_fn):
        import inspect, sqlite3, traceback
        params = _decode_json_env({_PARAMS_ENV_KEY!r}, {{}})
        ctx = ToolContext()
        if ctx.sqlite_db_path:
            os.makedirs(os.path.dirname(ctx.sqlite_db_path), exist_ok=True)
        sig = inspect.signature(run_fn)
        pos = [p for p in sig.parameters.values() if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        try:
            if len(pos) >= 2:
                result = run_fn(ctx, params) if pos[0].name.lower() in ("ctx", "context") else run_fn(params, ctx)
            elif len(pos) == 1:
                result = run_fn(ctx) if pos[0].name.lower() in ("ctx", "context") else run_fn(params)
            else:
                result = run_fn()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            raise
        if ctx.sqlite_db_path and os.path.exists(ctx.sqlite_db_path):
            try:
                conn = sqlite3.connect(ctx.sqlite_db_path)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.commit()
                conn.close()
            except Exception as exc:
                print(f"SQLite checkpoint failed: {{exc}}", file=sys.stderr)
        print(RESULT_MARKER + json.dumps({{"result": _json_safe(result)}}, default=str))
    """
)

CUSTOM_TOOL_BOOTSTRAP_COMMAND = (
    'RUNTIME_CACHE_ROOT="${SANDBOX_RUNTIME_CACHE_ROOT:-/tmp}" && \\\n'
    f'UV_CACHE_DIR="${{{_UV_CACHE_DIR_ENV_KEY}:-$RUNTIME_CACHE_ROOT/uv-cache}}" && \\\n'
    f'UV_INSTALL_DIR="${{{_UV_INSTALL_DIR_ENV_KEY}:-$RUNTIME_CACHE_ROOT/uv-bin}}" && \\\n'
    'XDG_CACHE_HOME="${XDG_CACHE_HOME:-$RUNTIME_CACHE_ROOT/xdg-cache}" && \\\n'
    'PIP_CACHE_DIR="${PIP_CACHE_DIR:-$RUNTIME_CACHE_ROOT/pip-cache}" && \\\n'
    'UV_TOOL_DIR="${UV_TOOL_DIR:-$RUNTIME_CACHE_ROOT/uv-tools}" && \\\n'
    'UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$RUNTIME_CACHE_ROOT/uv-project-env}" && \\\n'
    'mkdir -p "$UV_CACHE_DIR" "$UV_INSTALL_DIR" "$XDG_CACHE_HOME" "$PIP_CACHE_DIR" "$UV_TOOL_DIR" "$UV_PROJECT_ENVIRONMENT" && \\\n'
    'if ! command -v uv >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL="$UV_INSTALL_DIR" sh > /dev/null 2>&1; fi && \\\n'
    'export PATH="$UV_INSTALL_DIR:$PATH" && \\\n'
    "command -v uv >/dev/null 2>&1 && \\\n"
    f'SOURCE_EXEC_PATH="${{{_EXEC_SOURCE_PATH_ENV_KEY}:-.${_SOURCE_PATH_ENV_KEY}}}" && \\\n'
    "mkdir -p /tmp/_gobii && \\\n"
    "cat > /tmp/_gobii/_gobii_ctx.py <<'CTXEOF'\n"
    f"{_GOBII_CTX_MODULE}"
    "CTXEOF\n"
    'UV_CACHE_DIR="$UV_CACHE_DIR" UV_TOOL_DIR="$UV_TOOL_DIR" UV_PROJECT_ENVIRONMENT="$UV_PROJECT_ENVIRONMENT" XDG_CACHE_HOME="$XDG_CACHE_HOME" PIP_CACHE_DIR="$PIP_CACHE_DIR" PYTHONPATH=/tmp/_gobii:${PYTHONPATH:-} uv run --no-project "$SOURCE_EXEC_PATH"'
)


def is_custom_tools_available_for_agent(agent: Optional[PersistentAgent]) -> bool:
    return agent is not None and sandbox_compute_enabled_for_agent(agent)


def _agent_has_access(agent: PersistentAgent, filespace_id) -> bool:
    return AgentFileSpaceAccess.objects.filter(agent=agent, filespace_id=filespace_id).exists()


def _resolve_source_path(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    path = value.strip()
    if path.startswith("$[") and path.endswith("]"):
        path = path[2:-1].strip()
    if not path:
        return None
    if not path.startswith("/"):
        path = f"/{path}"
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if normalized in {"/", "/."}:
        return None
    return normalized


def _normalize_custom_tool_name(raw_name: Any) -> Optional[tuple[str, str]]:
    if not isinstance(raw_name, str):
        return None
    display_name = raw_name.strip()
    if not display_name:
        return None
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", display_name.replace("-", "_").replace(" ", "_").lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        return None
    max_slug_len = 128 - len(CUSTOM_TOOL_PREFIX)
    slug = slug[:max_slug_len].rstrip("_")
    if not slug:
        return None
    return display_name[:128], f"{CUSTOM_TOOL_PREFIX}{slug}"


def _normalize_parameters_schema(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    schema = dict(value)
    schema_type = schema.get("type")
    if schema_type in (None, ""):
        schema["type"] = "object"
    elif schema_type != "object":
        return None
    properties = schema.get("properties")
    if properties is None:
        schema["properties"] = {}
    elif not isinstance(properties, dict):
        return None
    required = schema.get("required")
    if required is None:
        schema["required"] = []
    elif not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return None
    return schema
def _normalize_timeout_seconds(value: Any) -> Optional[int]:
    if value in (None, ""):
        return DEFAULT_CUSTOM_TOOL_TIMEOUT_SECONDS
    if isinstance(value, bool):
        return None
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return None
    if timeout <= 0 or timeout > MAX_CUSTOM_TOOL_TIMEOUT_SECONDS:
        return None
    return timeout


def _get_filespace_file(agent: PersistentAgent, source_path: str) -> Optional[AgentFsNode]:
    try:
        filespace = get_or_create_default_filespace(agent)
    except Exception as exc:
        logger.error("Failed to resolve default filespace for agent %s: %s", agent.id, exc)
        return None

    if not _agent_has_access(agent, filespace.id):
        return None

    return (
        AgentFsNode.objects.alive()
        .filter(filespace=filespace, path=source_path)
        .first()
    )


def _read_source_text(agent: PersistentAgent, source_path: str) -> tuple[Optional[str], Optional[str]]:
    node = _get_filespace_file(agent, source_path)
    if node is None:
        return None, f"Source file not found: {source_path}"
    if node.node_type != AgentFsNode.NodeType.FILE:
        return None, f"Source path is not a file: {source_path}"
    if not node.content or not getattr(node.content, "name", None):
        return None, f"Source file has no content: {source_path}"

    max_size = min(get_max_file_size() or MAX_CUSTOM_TOOL_SOURCE_BYTES, MAX_CUSTOM_TOOL_SOURCE_BYTES)
    if node.size_bytes and node.size_bytes > max_size:
        return None, f"Source file exceeds the {max_size}-byte custom tool limit."

    try:
        with default_storage.open(node.content.name, "rb") as handle:
            raw = handle.read(max_size + 1)
    except OSError as exc:
        logger.error("Failed to read custom tool source %s for agent %s: %s", source_path, agent.id, exc)
        return None, "Failed to read the custom tool source file."

    if len(raw) > max_size:
        return None, f"Source file exceeds the {max_size}-byte custom tool limit."

    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "Custom tool source must be UTF-8 text."


def _sync_workspace_source(agent: PersistentAgent, source_path: str) -> Optional[Dict[str, Any]]:
    try:
        service = SandboxComputeService()
    except SandboxComputeUnavailable:
        return {
            "status": "error",
            "message": f"Sandbox workspace is unavailable; could not sync the latest source for {source_path}.",
        }

    if isinstance(service._backend, LocalSandboxBackend):
        return None

    try:
        session = service._ensure_session(agent, source="custom_tool_source_sync")
        sync_result = service._sync_workspace_push(agent, session)
    except Exception as exc:
        logger.warning(
            "Failed syncing sandbox workspace source for agent=%s path=%s",
            agent.id,
            source_path,
            exc_info=True,
        )
        return {
            "status": "error",
            "message": f"Failed to sync the latest sandbox workspace source for {source_path}: {exc}",
        }

    if isinstance(sync_result, dict) and sync_result.get("status") != "ok":
        return {
            "status": "error",
            "message": (
                f"Failed to sync the latest sandbox workspace source for {source_path}: "
                f"{sync_result.get('message') or 'sync failed'}"
            ),
            "sync_result": sync_result,
        }
    return None


def _validate_source_code(source_text: str, source_path: str) -> Optional[str]:
    source_bytes = source_text.encode("utf-8")
    if len(source_bytes) > MAX_CUSTOM_TOOL_SOURCE_BYTES:
        return f"Custom tool source must be {MAX_CUSTOM_TOOL_SOURCE_BYTES} bytes or smaller."
    try:
        tree = ast.parse(source_text, filename=source_path)
    except SyntaxError as exc:
        return f"Custom tool source has a syntax error: {exc}"

    has_run = any(isinstance(node, ast.FunctionDef) and node.name == "run" for node in tree.body)
    if not has_run:
        return "Custom tool source must define `def run(...):`."

    imports_main = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "_gobii_ctx"
        and any(alias.name == "main" and alias.asname is None for alias in node.names)
        for node in tree.body
    )
    if not imports_main:
        return "Custom tool source must import `main` with `from _gobii_ctx import main`."

    has_main_guard = False
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        if not (
            isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Eq)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "__main__"
        ):
            continue
        has_main_guard = any(
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "main"
            and len(stmt.value.args) == 1
            and isinstance(stmt.value.args[0], ast.Name)
            and stmt.value.args[0].id == "run"
            and not stmt.value.keywords
            for stmt in node.body
        )
        if has_main_guard:
            break
    if not has_main_guard:
        return "Custom tool source must end with `if __name__ == '__main__': main(run)`."
    return None


def _encode_env_json(value: Dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(value).encode("utf-8")).decode("ascii")


def _resolve_local_exec_source_path(agent: PersistentAgent, source_path: str) -> Optional[str]:
    node = _get_filespace_file(agent, source_path)
    if node is None or node.node_type != AgentFsNode.NodeType.FILE:
        return None
    if not node.content or not getattr(node.content, "name", None):
        return None

    try:
        return node.content.path
    except (AttributeError, NotImplementedError, OSError, ValueError):
        return None


def _resolve_bridge_base_url() -> str:
    configured = (getattr(settings, "PUBLIC_SITE_URL", "") or "").strip().rstrip("/")
    if configured:
        return configured

    try:
        current_site = Site.objects.get_current()
    except Exception:
        return ""

    domain = (getattr(current_site, "domain", "") or "").strip().rstrip("/")
    if not domain:
        return ""
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    scheme = "http" if "localhost" in domain or domain.startswith("127.") else "https"
    return f"{scheme}://{domain}"


def build_custom_tool_bridge_token(
    agent: PersistentAgent,
    tool: PersistentAgentCustomTool,
    *,
    parent_step_id: Optional[str] = None,
) -> str:
    payload = {
        "agent_id": str(agent.id),
        "tool_id": str(tool.id),
        "tool_name": tool.tool_name,
    }
    if parent_step_id:
        payload["parent_step_id"] = str(parent_step_id)
    return signing.dumps(
        payload,
        salt=CUSTOM_TOOL_BRIDGE_SALT,
        compress=True,
    )


def load_custom_tool_bridge_payload(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = signing.loads(
            token,
            salt=CUSTOM_TOOL_BRIDGE_SALT,
            max_age=CUSTOM_TOOL_BRIDGE_TTL_SECONDS,
        )
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


@contextlib.contextmanager
def _custom_tool_sqlite_db(agent: PersistentAgent):
    existing_db_path = get_sqlite_db_path()
    if existing_db_path:
        yield existing_db_path
        return

    with agent_sqlite_db(str(agent.id)) as db_path:
        yield db_path


@contextlib.contextmanager
def _custom_tool_uv_runtime_dirs(service: SandboxComputeService):
    if not isinstance(service._backend, LocalSandboxBackend):
        yield {}
        return

    with tempfile.TemporaryDirectory(prefix="gobii-custom-tool-runtime-") as runtime_root:
        runtime_env = {
            _RUNTIME_CACHE_ROOT_ENV_KEY: runtime_root,
            _UV_CACHE_DIR_ENV_KEY: os.path.join(runtime_root, "uv-cache"),
            _UV_INSTALL_DIR_ENV_KEY: os.path.join(runtime_root, "uv-bin"),
            "HOME": os.path.join(runtime_root, "home"),
            "TMPDIR": os.path.join(runtime_root, "tmp"),
        }
        for path in runtime_env.values():
            os.makedirs(path, exist_ok=True)
        yield runtime_env


def get_create_custom_tool_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_custom_tool",
            "description": (
                "Create or update a sandboxed Python custom tool for this agent. "
                "Prefer custom tools over manual multi-step procedures — they are far more efficient. "
                "Write tools eagerly: if work involves 3+ steps, loops, data transforms, or API calls, make a tool. "
                "Small disposable tools are good; do not over-engineer. "
                "Source is a complete Python script run via `uv run`. Structure:\n"
                "1. PEP 723 metadata at top for third-party deps (if any): `# /// script\\n# dependencies = [\"requests\"]\\n# ///`\n"
                "2. `from _gobii_ctx import main` (plus ToolContext if you need type hints)\n"
                "3. `def run(params, ctx): ...` — your tool logic, return JSON-serializable data\n"
                "4. `if __name__ == '__main__': main(run)`\n"
                "ctx.call_tool(name, params) invokes any agent tool (MCP, builtins, other custom_* tools). "
                "Write results directly to SQLite via ctx.sqlite_db_path instead of returning large intermediate data. "
                "Do not manually repeat MCP/tool/API calls or paste long SQL insert/update lists in your own loop — put the loop in Python and use bulk writes in one transaction. "
                "SECRETS: API keys, DB credentials, and sensitive values are in os.environ — always use them, never hardcode. "
                "PROXY: All non-proxy network traffic is blocked — outbound requests WILL fail without the proxy. "
                "For direct outbound requests, use SOCKS5-capable libraries (requests[socks], httpx[socks]) "
                "or subprocess curl, and read `ALL_PROXY`, `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` from os.environ. "
                "For tool-to-tool calls, use ctx.call_tool() — it handles the internal bridge transport for you, so do not manage proxy logic yourself. "
                "Simplest flow: write `/tools/my_tool.py`, then call create_custom_tool with that same `source_path`. "
                "Latest workspace edits are synced automatically before registration and execution. "
                "Prefer patching the same file and reusing the same tool over creating near-duplicate tools. "
                "Provide `source_code` to write the file now, or point at an existing `.py` file in the workspace. "
                "The saved tool gets a canonical id like `custom_my_tool` and is enabled by default."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short tool name. The canonical tool id is derived from this name.",
                    },
                    "description": {
                        "type": "string",
                        "description": "What the custom tool does and when to use it.",
                    },
                    "source_path": {
                        "type": "string",
                        "description": "Workspace path to the Python source file, for example `/tools/my_tool.py`.",
                    },
                    "source_code": {
                        "type": "string",
                        "description": "Optional full Python source. When provided, it overwrites `source_path` before registration.",
                    },
                    "parameters_schema": {
                        "type": "object",
                        "description": "JSON schema for tool input params. Use {\"type\": \"object\", \"properties\": {}} if no params needed.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional sandbox timeout in seconds for this tool (default 300, max 900).",
                    },
                    "enable": {
                        "type": "boolean",
                        "description": "When true (default), enable the saved custom tool immediately.",
                    },
                },
                "required": ["name", "description", "source_path", "parameters_schema"],
            },
        },
    }


def execute_create_custom_tool(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    if not is_custom_tools_available_for_agent(agent):
        return {"status": "error", "message": "Custom tools require sandbox compute."}

    normalized_name = _normalize_custom_tool_name(params.get("name"))
    if normalized_name is None:
        return {"status": "error", "message": "name must be a non-empty string."}
    display_name, tool_name = normalized_name

    description = params.get("description")
    if not isinstance(description, str) or not description.strip():
        return {"status": "error", "message": "description must be a non-empty string."}
    description = description.strip()

    source_path = _resolve_source_path(params.get("source_path"))
    if not source_path:
        return {"status": "error", "message": "source_path must be a valid workspace path like `/tools/my_tool.py`."}
    if not source_path.endswith(".py"):
        return {"status": "error", "message": "source_path must point to a `.py` file."}

    entrypoint_param = params.get("entrypoint")
    if entrypoint_param not in (None, "", "run"):
        return {
            "status": "error",
            "message": "entrypoint is no longer configurable. Custom tools must use `def run(...):` and `main(run)`.",
        }

    parameters_schema = _normalize_parameters_schema(params.get("parameters_schema"))
    if parameters_schema is None:
        return {
            "status": "error",
            "message": "parameters_schema must be a JSON object schema with `type: object`.",
        }

    timeout_seconds = _normalize_timeout_seconds(params.get("timeout_seconds"))
    if timeout_seconds is None:
        return {
            "status": "error",
            "message": f"timeout_seconds must be between 1 and {MAX_CUSTOM_TOOL_TIMEOUT_SECONDS}.",
        }

    enable_value = params.get("enable", True)
    if enable_value is None:
        enable_tool = True
    elif isinstance(enable_value, bool):
        enable_tool = enable_value
    elif isinstance(enable_value, str):
        lowered = enable_value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            enable_tool = True
        elif lowered in {"false", "0", "no"}:
            enable_tool = False
        else:
            return {"status": "error", "message": "enable must be a boolean when provided."}
    else:
        return {"status": "error", "message": "enable must be a boolean when provided."}

    source_code = params.get("source_code")
    if source_code is not None and not isinstance(source_code, str):
        return {"status": "error", "message": "source_code must be a string when provided."}

    if isinstance(source_code, str):
        validation_error = _validate_source_code(source_code, source_path)
        if validation_error:
            return {"status": "error", "message": validation_error}
        write_result = write_bytes_to_dir(
            agent=agent,
            content_bytes=source_code.encode("utf-8"),
            extension=".py",
            mime_type="text/x-python",
            path=source_path,
            overwrite=True,
        )
        if write_result.get("status") != "ok":
            return write_result
    else:
        sync_error = _sync_workspace_source(agent, source_path)
        if sync_error:
            return sync_error
        source_text, source_error = _read_source_text(agent, source_path)
        if source_error:
            return {"status": "error", "message": source_error}
        assert source_text is not None
        validation_error = _validate_source_code(source_text, source_path)
        if validation_error:
            return {"status": "error", "message": validation_error}

    tool, created = PersistentAgentCustomTool.objects.update_or_create(
        agent=agent,
        tool_name=tool_name,
        defaults={
            "name": display_name,
            "description": description,
            "source_path": source_path,
            "parameters_schema": parameters_schema,
            "entrypoint": "run",
            "timeout_seconds": timeout_seconds,
        },
    )

    enable_result = {"enabled": [], "already_enabled": [], "evicted": [], "invalid": []}
    if enable_tool:
        from .tool_manager import enable_tools

        enable_result = enable_tools(agent, [tool.tool_name])

    action = "Created" if created else "Updated"
    message = f"{action} custom tool `{tool.tool_name}`."
    if enable_tool:
        parts = []
        if enable_result.get("enabled"):
            parts.append(f"Enabled: {', '.join(enable_result['enabled'])}")
        if enable_result.get("already_enabled"):
            parts.append(f"Already enabled: {', '.join(enable_result['already_enabled'])}")
        if enable_result.get("evicted"):
            parts.append(f"Evicted (LRU): {', '.join(enable_result['evicted'])}")
        if parts:
            message += " " + "; ".join(parts)

    return {
        "status": "ok",
        "message": message,
        "created": created,
        "tool_name": tool.tool_name,
        "name": tool.name,
        "source_path": tool.source_path,
        "timeout_seconds": tool.timeout_seconds,
        "enabled": enable_result.get("enabled", []),
        "already_enabled": enable_result.get("already_enabled", []),
        "evicted": enable_result.get("evicted", []),
        "invalid": enable_result.get("invalid", []),
    }


def _parse_custom_tool_result(stdout: str) -> tuple[Optional[Any], str]:
    cleaned_lines = []
    parsed_result = None
    for line in (stdout or "").splitlines():
        if line.startswith(CUSTOM_TOOL_RESULT_MARKER):
            raw_payload = line[len(CUSTOM_TOOL_RESULT_MARKER):]
            try:
                parsed = json.loads(raw_payload)
            except json.JSONDecodeError:
                return None, stdout or ""
            parsed_result = parsed.get("result")
            continue
        cleaned_lines.append(line)
    return parsed_result, "\n".join(cleaned_lines).strip()


def execute_custom_tool(agent: PersistentAgent, tool: PersistentAgentCustomTool, params: Dict[str, Any]) -> Dict[str, Any]:
    if not is_custom_tools_available_for_agent(agent):
        return {"status": "error", "message": "Custom tools require sandbox compute."}

    sync_error = _sync_workspace_source(agent, tool.source_path)
    if sync_error:
        return sync_error

    source_text, source_error = _read_source_text(agent, tool.source_path)
    if source_error:
        return {"status": "error", "message": source_error}
    assert source_text is not None

    validation_error = _validate_source_code(source_text, tool.source_path)
    if validation_error:
        return {"status": "error", "message": validation_error}

    base_url = _resolve_bridge_base_url()
    if not base_url:
        return {"status": "error", "message": "PUBLIC_SITE_URL or Site domain is required to run custom tools."}

    execution_context = get_tool_execution_context()
    parent_step_id = execution_context.step_id if execution_context is not None else None
    bridge_url = f"{base_url}{reverse('api:custom-tool-bridge-execute')}"
    env = {
        _PARAMS_ENV_KEY: _encode_env_json(params or {}),
        _BRIDGE_URL_ENV_KEY: bridge_url,
        _TOKEN_ENV_KEY: build_custom_tool_bridge_token(agent, tool, parent_step_id=parent_step_id),
        _TOOL_NAME_ENV_KEY: tool.tool_name,
        _SOURCE_PATH_ENV_KEY: tool.source_path,
    }

    try:
        service = SandboxComputeService()
    except SandboxComputeUnavailable as exc:
        return {"status": "error", "message": str(exc)}

    if isinstance(service._backend, LocalSandboxBackend):
        local_exec_source_path = _resolve_local_exec_source_path(agent, tool.source_path)
        if local_exec_source_path:
            env[_EXEC_SOURCE_PATH_ENV_KEY] = local_exec_source_path

    with _custom_tool_uv_runtime_dirs(service) as runtime_env, _custom_tool_sqlite_db(agent) as sqlite_db_path:
        if runtime_env:
            env.update(runtime_env)
        result = service.run_custom_tool_command(
            agent,
            CUSTOM_TOOL_BOOTSTRAP_COMMAND,
            env=env,
            timeout=tool.timeout_seconds,
            interactive=False,
            local_sqlite_db_path=sqlite_db_path,
            sqlite_env_key=_SQLITE_DB_PATH_ENV_KEY,
        )
    if not isinstance(result, dict):
        return {"status": "error", "message": "Custom tool execution returned an invalid sandbox response."}
    if result.get("status") == "error":
        return result

    parsed_result, cleaned_stdout = _parse_custom_tool_result(result.get("stdout", ""))
    if parsed_result is None:
        return {
            "status": "error",
            "message": "Custom tool did not return a result. Ensure the script ends with `if __name__ == '__main__': main(run)` and returns JSON-serializable data.",
            "stdout": cleaned_stdout,
            "stderr": result.get("stderr", ""),
        }

    response = {
        "status": "ok",
        "result": parsed_result,
    }
    if cleaned_stdout:
        response["stdout"] = cleaned_stdout
    if result.get("stderr"):
        response["stderr"] = result.get("stderr")
    return response


def format_recent_custom_tools_for_prompt(agent: PersistentAgent, limit: int = 3) -> str:
    if limit <= 0:
        return ""
    tools = list(
        PersistentAgentCustomTool.objects.filter(agent=agent)
        .order_by("-updated_at", "tool_name")[:limit]
    )
    if not tools:
        return ""

    lines = []
    for tool in tools:
        description = (tool.description or "").strip() or "(no description)"
        if len(description) > 120:
            description = description[:117].rstrip() + "..."
        lines.append(f"- {tool.tool_name}: {description} (source: {tool.source_path})")
    return "\n".join(lines)


def get_custom_tools_prompt_summary(agent: PersistentAgent, *, recent_limit: int = 3) -> str:
    if not is_custom_tools_available_for_agent(agent):
        return ""

    total = PersistentAgentCustomTool.objects.filter(agent=agent).count()
    enabled = PersistentAgentEnabledTool.objects.filter(
        agent=agent,
        tool_full_name__startswith=CUSTOM_TOOL_PREFIX,
    ).count()
    summary = (
        f"Custom tools: {total} saved, {enabled} enabled. "
        "Default mode for repetitive or bulk work: write or patch a custom tool first. "
        "Discoverable via search_tools; share the enabled-tool limit. "
        "\nPHILOSOPHY: Never shuttle data through your context when a tool can handle it directly. "
        "Passing intermediate results back to you for processing wastes tokens, loses fidelity, and adds latency. "
        "Custom tools run Python at machine speed with full data precision — you decide WHAT to do, the tool DOES it. "
        "Write a tool that fetches, transforms, and stores data in SQLite, then read back a summary. "
        "Your job is to orchestrate, not to manually iterate over rows or transform JSON in your context. "
        "A short one-off tool is usually better than manual repetition. "
        "\nWHEN TO CREATE: Whenever work involves multiple steps, data processing, API calls, loops, or batch operations. "
        "If you're about to chain 3+ tool calls or handle intermediate data between steps — stop and write a tool instead. "
        "Immediate triggers: repeated MCP/API calls, pagination/cursors, sync/import jobs, bulk INSERT/UPDATE/UPSERT work, row-by-row transforms, retries/backoff, or checkpoint/resume flows. "
        "Bias toward creating tools early — they are cheap to write, test, and iterate on. "
        "\nDEV LOOP: write `/tools/my_tool.py` -> create_custom_tool(source_path='/tools/my_tool.py', ...) -> "
        "invoke the custom_* tool -> inspect result/error -> patch the file -> re-invoke. "
        "Latest workspace edits are synced automatically before registration and execution. "
        "Prefer patching the same file over creating near-duplicate tools. "
        "Jump straight in — don't ask, just write the tool and run it. "
        "Start with a small sample/limit, verify a few rows, then widen scope. "
        "\nSOURCE: Scripts are run via `uv run` — any pip package is available. "
        "Add PEP 723 metadata at the top for third-party deps: "
        "# /// script\\n# dependencies = [\"requests\"]\\n# ///\\n"
        "Structure: `from _gobii_ctx import main` at top, `def run(params, ctx): ...` for logic, "
        "`if __name__ == '__main__': main(run)` at bottom. That's it. "
        "\nTEMPLATE:\\n"
        "```\\n"
        "# /// script\\n"
        "# dependencies = [\"some-package\"]\\n"
        "# ///\\n"
        "import os, sqlite3\\n"
        "from _gobii_ctx import main\\n\\n"
        "def run(params, ctx):\\n"
        "    # Use os.environ for secrets (API keys, DB creds, etc.)\\n"
        "    # Use ctx.call_tool(name, params) to call other agent tools\\n"
        "    # Write results to SQLite instead of returning large data:\\n"
        "    db = sqlite3.connect(ctx.sqlite_db_path)\\n"
        "    db.execute('CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, value TEXT)')\\n"
        "    db.executemany('INSERT OR REPLACE INTO results VALUES (?, ?)', rows)\\n"
        "    db.commit()\\n"
        "    return {'rows_written': len(rows)}\\n\\n"
        "if __name__ == '__main__':\\n"
        "    main(run)\\n"
        "```\\n"
        "\nSQLITE-FIRST: Write results directly to ctx.sqlite_db_path using sqlite3 instead of returning large data. "
        "Your custom tool shares the agent's embedded SQLite DB — INSERT/UPDATE/SELECT directly. "
        "This is far more efficient than passing intermediate results back to the agent for processing. "
        "Pattern: tool fetches data -> normalizes it -> writes to SQLite tables -> returns a summary. "
        "\nTOOL ORCHESTRATION: ctx.call_tool(name, params) invokes any agent tool (MCP, builtins, other custom_* tools) "
        "and returns the result dict. Use this to build pipelines entirely inside a custom tool: "
        "e.g., call search/scrape tools in a loop, process results, store in SQLite — all in one tool execution. "
        "\nSECRETS: API keys, DB connection strings, auth tokens, and all sensitive values are available as "
        "env vars via os.environ. ALWAYS use secrets for credentials — never hardcode them. "
        "Use the exact env var names shown in the secrets/env_var configuration. "
        "\nPROXY: All non-proxy network traffic is blocked — outbound requests WILL fail without the proxy. "
        "The proxy is SOCKS5. For direct outbound requests in your own code, "
        "use SOCKS5-capable libraries (requests[socks], httpx[socks]) and read `ALL_PROXY`, `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` from os.environ. "
        "subprocess curl honors these proxy env vars automatically. "
        "For tool-to-tool calls, always use ctx.call_tool() — it handles the internal bridge transport for you, so do not manage proxy logic yourself. "
        "\nANTI-PATTERNS: do not spend a turn manually making dozens of near-identical MCP/API calls, manually pasting rows into sqlite_batch INSERT statements, or using your context to transform large JSON payloads. "
        "Write a tool and batch the work. "
        "\nSANDBOX TOOLS: rg, fd, jq, sqlite3, sed, awk, file, tar, unzip, fzf, yq, git are available via subprocess. "
        "Agent filespace contents are synced into the sandbox before each run. "
        "\nPATTERNS:"
        "\n- Data sync to SQLite: fetch data from external sources (APIs, scraping, MCP tools) -> normalize -> "
        "conn = sqlite3.connect(ctx.sqlite_db_path); conn.execute('CREATE TABLE IF NOT EXISTS ...'); "
        "conn.executemany('INSERT OR REPLACE INTO ...', rows); conn.commit() -> return {'rows_synced': len(rows)}. "
        "The agent can then query this table via sqlite_batch without re-fetching. "
        "This is the most common pattern — sync once, query many times."
        "\n- Bulk read & process from SQLite: read existing agent data from SQLite, transform, enrich, "
        "aggregate, or export it. conn = sqlite3.connect(ctx.sqlite_db_path); "
        "rows = conn.execute('SELECT ...').fetchall(); process rows in Python (join, filter, compute) -> "
        "write results back to new SQLite tables or return a summary. "
        "Use this to derive insights, build reports, or prepare data for export without manual row-by-row tool calls."
        "\n- Tool composition: call multiple tools inside one custom tool: "
        "results = [ctx.call_tool('mcp_brightdata_search_engine', {'query': q}) for q in queries]; "
        "process all results, write to SQLite, return summary. "
        "One custom tool call replaces dozens of manual tool calls."
        "\n- Bulk MCP fan-out: iterate ctx.call_tool(...) for many ids/queries/pages inside Python, normalize the results, "
        "then use executemany with INSERT OR REPLACE/UPSERT inside one transaction. "
        "\n- Custom tool chains: custom tools can call other custom tools via ctx.call_tool('custom_other_tool', params). "
        "Build layered pipelines: one tool syncs data, another transforms, another exports."
        "\n- Authenticated API sync: read tokens from os.environ -> paginate through API -> "
        "upsert rows into SQLite -> return count."
        "\n- Checkpointed orchestration: loop over items -> call tools -> "
        "record progress in SQLite -> resume safely after failures or timeouts."
        "\n- Safe dev loop: start with a small sample -> inspect output -> file_str_replace to patch -> widen scope."
        "\nOnce stable, save the workflow as a skill referencing the canonical custom_* tool id."
    )

    recent = format_recent_custom_tools_for_prompt(agent, limit=recent_limit)
    if recent:
        summary += "\nRecent custom tools:\n" + recent
    return summary
