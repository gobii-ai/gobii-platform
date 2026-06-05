import ast
import base64
import builtins
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
from api.agent.tools.custom_tool_names import CREATE_CUSTOM_TOOL_NAME
from api.agent.tools.sqlite_state import agent_sqlite_db, get_sqlite_db_path
from api.agent.tools.runtime_execution_context import get_tool_execution_context
from api.utils.json_schema import (
    normalize_parameters_schema,
    sanitize_tool_parameters_schema_for_llm,
)
from api.services.sandbox_compute import (
    LocalSandboxBackend,
    SandboxComputeService,
    SandboxComputeUnavailable,
    custom_tool_workspace_root_for_backend,
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
CUSTOM_TOOL_RETRY_CHECKLIST = (
    "Patch all validation issues before retrying: exact import, exact final line, referenced imports, "
    "required params, remaining_work/next_cursor, and do_not_repeat_manually when writes happen."
)

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
    import contextlib
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

        def proxy_url(self):
            for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
                value = os.environ.get(key, "")
                if value:
                    return value
            return ""

        def requests_proxies(self):
            proxy = self.proxy_url()
            if not proxy:
                return {{}}
            return {{"http": proxy, "https": proxy}}

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
                result = json.loads(raw or "{{}}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Tool bridge returned invalid JSON: {{raw[:500]}}") from exc
            if isinstance(result, dict) and result.get("custom_tool_abort") is True:
                raise RuntimeError(result.get("message") or "Custom tool stopped by bridge.")
            return result

        @contextlib.contextmanager
        def sqlite(self):
            import sqlite3

            if not self.sqlite_db_path:
                raise RuntimeError("ctx.sqlite_db_path is unavailable.")
            os.makedirs(os.path.dirname(self.sqlite_db_path), exist_ok=True)
            conn = sqlite3.connect(self.sqlite_db_path)
            try:
                conn.execute("PRAGMA busy_timeout=5000;")
            except Exception:
                pass
            try:
                yield conn
                conn.commit()
            except Exception:
                with contextlib.suppress(Exception):
                    conn.rollback()
                raise
            finally:
                conn.close()

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


def _normalize_custom_tool_source_path(value: Any) -> Optional[str]:
    normalized = _resolve_source_path(value)
    if not normalized or not normalized.endswith(".py"):
        return None
    return normalized


def normalize_custom_tool_source_path(value: Any) -> Optional[str]:
    return _normalize_custom_tool_source_path(value)


def _normalize_custom_tool_name(raw_name: Any) -> Optional[tuple[str, str]]:
    if not isinstance(raw_name, str):
        return None
    display_name = raw_name.strip()
    if not display_name:
        return None
    normalized_input = display_name.lower()
    if normalized_input.startswith(CUSTOM_TOOL_PREFIX):
        normalized_input = normalized_input[len(CUSTOM_TOOL_PREFIX):]
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", normalized_input.replace("-", "_").replace(" ", "_"))
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        return None
    max_slug_len = 128 - len(CUSTOM_TOOL_PREFIX)
    slug = slug[:max_slug_len].rstrip("_")
    if not slug:
        return None
    return display_name[:128], f"{CUSTOM_TOOL_PREFIX}{slug}"


def normalize_custom_tool_name(raw_name: Any) -> Optional[tuple[str, str]]:
    return _normalize_custom_tool_name(raw_name)


def normalize_custom_tool_parameters_schema(value: Any) -> Optional[Dict[str, Any]]:
    return normalize_parameters_schema(value)


def _normalize_custom_tool_entrypoint(value: Any) -> Optional[str]:
    if value in (None, "", "run"):
        return "run"
    return None


def normalize_custom_tool_entrypoint(value: Any) -> Optional[str]:
    return _normalize_custom_tool_entrypoint(value)


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


def normalize_custom_tool_timeout_seconds(value: Any) -> Optional[int]:
    return _normalize_timeout_seconds(value)


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


def read_custom_tool_source_text(agent: PersistentAgent, source_path: str) -> tuple[Optional[str], Optional[str]]:
    return _read_source_text(agent, source_path)


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
        pull_result = service._sync_workspace_paths_pull(agent, session, paths=[source_path])
        if isinstance(pull_result, dict) and pull_result.get("status") != "ok":
            return {
                "status": "error",
                "message": (
                    f"Failed to sync the latest sandbox workspace source for {source_path}: "
                    f"{pull_result.get('message') or 'source pull failed'}"
                ),
                "sync_result": pull_result,
            }
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


_PYTHON_BUILTIN_NAMES = frozenset(dir(builtins))


def _target_bound_names(target: ast.AST) -> set[str]:
    return {node.id for node in ast.walk(target) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}


def _import_bound_name(alias: ast.alias) -> str:
    return alias.asname or alias.name.split(".", 1)[0]


def _module_bound_names(tree: ast.Module) -> set[str]:
    names = {"__name__"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(_import_bound_name(alias) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(_import_bound_name(alias) for alias in node.names if alias.name != "*")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_target_bound_names(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(_target_bound_names(node.target))
        elif isinstance(node, ast.AugAssign):
            names.update(_target_bound_names(node.target))
    return names


def _function_bound_names(node: ast.FunctionDef | ast.AsyncFunctionDef, module_names: set[str]) -> set[str]:
    names = set(module_names)
    args = node.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        names.add(arg.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)

    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.add(child.id)
        elif isinstance(child, ast.Import):
            names.update(_import_bound_name(alias) for alias in child.names)
        elif isinstance(child, ast.ImportFrom):
            names.update(_import_bound_name(alias) for alias in child.names if alias.name != "*")
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
    return names


def _fstring_load_names(value: ast.AST) -> set[str]:
    load_names: set[str] = set()
    local_bindings: set[str] = set()

    class FormattedValueNameVisitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                load_names.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                local_bindings.add(node.id)

    FormattedValueNameVisitor().visit(value)
    return load_names - local_bindings


def _find_likely_undefined_fstring_names(tree: ast.Module) -> list[str]:
    module_names = _module_bound_names(tree)
    missing: set[str] = set()

    def check_joined_strings(scope: ast.AST, bound_names: set[str]) -> None:
        allowed_names = bound_names | _PYTHON_BUILTIN_NAMES

        class FStringScopeVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                if node is scope:
                    self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                if node is scope:
                    self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                return

            def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
                missing.update(name for name in _fstring_load_names(node.value) if name not in allowed_names)
                self.generic_visit(node)

        FStringScopeVisitor().visit(scope)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_joined_strings(node, _function_bound_names(node, module_names))

    return sorted(missing)


_ACTIONABLE_RESULT_TERMS = (
    "next_action",
    "next action",
    "follow_up",
    "follow-up",
    "verify",
    "verification",
    "read-only",
    "instructions",
    "remaining_work",
    "next_cursor",
)

_ACTIONABLE_RESULT_REQUIRED_LITERAL_TRIGGERS = (
    "ctx.call_tool",
    "ctx.sqlite",
    "sqlite3",
    ".execute(",
    ".executemany(",
    "batch_size",
    "batch_id",
    "remaining",
    "cursor",
    "side_effect",
    "output_table",
)

_ACTIONABLE_RESULT_REQUIRED_WORD_TRIGGERS = (
    "insert",
    "update",
    "delete",
    "upsert",
    "append",
    "sync",
)

_BATCH_RESULT_REQUIRED_TRIGGERS = (
    "batch_size",
    "batch_limit",
    "limit",
    "max_items",
    "max_rows",
)

_BATCH_PROGRESS_TERMS = (
    "remaining_work",
    "remaining",
    "next_cursor",
    "cursor",
)

_REPLAY_PREVENTION_TERMS = (
    "read-only",
    "read only",
    "do not repeat",
    "do not replay",
    "do not manually",
    "not another append",
    "not replay",
)
_DO_NOT_REPEAT_TRUE_RE = re.compile(
    r"['\"]?do_not_repeat_manually['\"]?\s*[:=]\s*(?:True|true|1)\b"
)
_SIDE_EFFECT_CONTEXT_TERMS = ("side_effect", "sheet", "worksheet", "external", "ctx.call_tool", "call_tool")
_SIDE_EFFECT_ACTION_RE = re.compile(
    r"\b(?:append(?:ed|ing)?|sync(?:ed|ing)?|insert(?:ed|ing)?|update(?:d|ing)?|"
    r"upsert(?:ed|ing)?|delete(?:d|ing)?|write|writes|wrote|written)\b"
)


def _source_string_literals(tree: ast.Module) -> set[str]:
    return {
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _source_identifiers(tree: ast.Module) -> set[str]:
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id.lower())
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg.lower())
    return identifiers


def _needs_actionable_result_signal(source_text: str) -> bool:
    source_lower = source_text.lower()
    return any(
        term in source_lower for term in _ACTIONABLE_RESULT_REQUIRED_LITERAL_TRIGGERS
    ) or any(
        re.search(rf"\b{re.escape(term)}\b", source_lower)
        for term in _ACTIONABLE_RESULT_REQUIRED_WORD_TRIGGERS
    )


def _has_actionable_result_signal(tree: ast.Module) -> bool:
    literals = _source_string_literals(tree)
    return any(term in literal for literal in literals for term in _ACTIONABLE_RESULT_TERMS)


def _needs_batch_progress_signal(source_text: str) -> bool:
    source_lower = source_text.lower()
    specific_batch_triggers = tuple(
        term for term in _BATCH_RESULT_REQUIRED_TRIGGERS if term != "limit"
    )
    if any(term in source_lower for term in specific_batch_triggers):
        return True
    return "limit" in source_lower and any(
        term in source_lower
        for term in (
            "batch",
            "backfill",
            "cursor",
            "fanout",
            "fan-out",
            "pagination",
            "page_token",
            "offset",
            "remaining",
            "sync",
        )
    )


def _has_batch_progress_signal(tree: ast.Module) -> bool:
    terms = _source_string_literals(tree) | _source_identifiers(tree)
    return any(progress_term in term for term in terms for progress_term in _BATCH_PROGRESS_TERMS)


def _needs_replay_prevention_signal(source_text: str) -> bool:
    source_lower = source_text.lower()
    if not _SIDE_EFFECT_ACTION_RE.search(source_lower):
        return False

    if "remaining_work" in source_lower or "next_cursor" in source_lower:
        return False

    if any(term in source_lower for term in ("side_effect", "sheet", "worksheet", "external")):
        return True

    if "ctx.call_tool" in source_lower or "call_tool" in source_lower:
        return not any(term in source_lower for term in ("remaining_work", "next_cursor"))

    return False


def _has_replay_prevention_signal(source_text: str) -> bool:
    source_lower = source_text.lower()
    return bool(_DO_NOT_REPEAT_TRUE_RE.search(source_text)) or any(term in source_lower for term in _REPLAY_PREVENTION_TERMS)


def _node_contains_url_sample_literal(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and ("http://" in child.value.lower() or "https://" in child.value.lower())
        ):
            return True
    return False


def _is_params_get_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "params"
    )


def _has_builtin_url_sample_default(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if _is_params_get_call(node) and len(node.args) >= 2 and _node_contains_url_sample_literal(node.args[1]):
            return True
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        if not node.values or not _is_params_get_call(node.values[0]):
            continue
        if any(_node_contains_url_sample_literal(value) for value in node.values[1:]):
            return True
    return False


def _result_has_replay_prevention(payload: Dict[str, Any]) -> bool:
    if payload.get("do_not_repeat_manually") is True:
        return True
    text = json.dumps(payload, sort_keys=True, default=str).lower()
    return any(term in text for term in _REPLAY_PREVENTION_TERMS)


def _result_indicates_completed_side_effect(payload: Dict[str, Any]) -> bool:
    if payload.get("dry_run") is True:
        return False
    status = str(payload.get("status") or "").lower()
    if "dry_run" in status or "partial" in status:
        return False
    text = json.dumps(payload, sort_keys=True, default=str).lower()
    if not _SIDE_EFFECT_ACTION_RE.search(text):
        return False
    if payload.get("remaining_work") in (False, 0, "0", "false", "False"):
        return True
    return "complete" in status or "success" in status or payload.get("side_effects_completed") is True


def _guard_custom_tool_result_replay(source_text: str, parsed_result: Any) -> Any:
    if not isinstance(parsed_result, dict) or _result_has_replay_prevention(parsed_result):
        return parsed_result

    source_lower = source_text.lower()
    result_text = json.dumps(parsed_result, sort_keys=True, default=str).lower()
    side_effect_context = any(term in source_lower or term in result_text for term in _SIDE_EFFECT_CONTEXT_TERMS)
    if not side_effect_context or not _result_indicates_completed_side_effect(parsed_result):
        return parsed_result

    guarded_result = dict(parsed_result)
    replay_guidance = "Do not repeat manually; verify read-only; do not append/add/update again."
    guarded_result["do_not_repeat_manually"] = True
    next_action = guarded_result.get("next_action")
    if isinstance(next_action, str) and next_action.strip():
        guarded_result["next_action"] = f"{replay_guidance} {next_action.strip()}"
    else:
        guarded_result["next_action"] = replay_guidance
    return guarded_result


def _invalid_datetime_timezone_refs(tree: ast.Module) -> list[str]:
    datetime_class_names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module != "datetime":
            continue
        for alias in node.names:
            if alias.name == "datetime":
                datetime_class_names.add(alias.asname or alias.name)

    invalid_refs: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in {"timezone", "UTC"}
            and isinstance(node.value, ast.Name)
            and node.value.id in datetime_class_names
        ):
            invalid_refs.add(f"{node.value.id}.{node.attr}")
    return sorted(invalid_refs)


def _has_match_end_url_slice(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        target = node.value
        if not isinstance(target, ast.Name) or "url" not in target.id.lower():
            continue
        slice_node = node.slice
        if not isinstance(slice_node, ast.Slice):
            continue
        start = slice_node.lower
        if (
            isinstance(start, ast.Call)
            and isinstance(start.func, ast.Attribute)
            and start.func.attr == "end"
        ):
            return True
    return False


def _has_url_regex_remainder_antipattern(tree: ast.Module) -> bool:
    string_literals = _source_string_literals(tree)
    has_url_segment_pattern = any(
        ("://" in literal or "http" in literal)
        and "/" in literal
        and any(
            marker in literal.replace(" ", "")
            for marker in (
                "[^/]+",
                "[a-za-z0-9_-]+",
                "[a-za-z0-9-_]+",
                "[\\w-]+",
                "[\\w_-]+",
                "\\w+",
            )
        )
        for literal in string_literals
    )
    if not has_url_segment_pattern:
        return False

    return _has_match_end_url_slice(tree)


def _validate_schema_runtime_params_for_source(
    source_text: str,
    description: str,
    parameters_schema: Dict[str, Any],
) -> Optional[str]:
    properties = parameters_schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return None

    combined_text = "\n".join(
        (
            source_text.lower(),
            description.lower(),
            json.dumps(parameters_schema, sort_keys=True).lower(),
        )
    )
    url_specific_markers = ("candidate url", "candidate_urls", "input_urls", "direct_post_urls", "scrape_ready_urls", "url classifier", "url validator", "linkedin", "/posts/", "fully qualified url", "scrape-ready")
    is_url_or_list_validator = "url" in combined_text and any(marker in combined_text for marker in url_specific_markers)
    if not is_url_or_list_validator:
        return None

    input_like_names = {
        "urls", "url", "domains", "domain",
        "input_urls", "input_domains", "input_data", "input_values", "inputs", "items",
        "candidates", "candidate_urls", "candidate_url", "candidate_domains", "candidate_domain",
        "candidate_inputs", "candidate_list", "company_domains",
        "input_table",
        "source_table",
        "output_table",
        "dest_table",
        "min_posts",
        "minimum_posts",
        "minimum_count",
        "limit",
        "batch_size",
    }
    property_names = set(properties)
    if not property_names & input_like_names:
        return None

    required = parameters_schema.get("required")
    required_names = {name for name in required if isinstance(name, str)} if isinstance(required, list) else set()
    if required_names & input_like_names:
        return None
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return None
    if not _has_builtin_url_sample_default(tree):
        return None

    return (
        "URL/list validator custom tools must require explicit runtime inputs instead of relying on built-in samples "
        "or hidden defaults. Mark urls/domains/candidates/input_table/source_table plus output_table/dest_table/minimum/limit params as required as appropriate, then "
        "invoke the tool with concrete values. Patch all validation issues before retrying: undefined names, remaining_work/next_cursor, explicit inputs, and do_not_repeat_manually when writes happen."
    )


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
        return (
            "Custom tool source must import `main` with `from _gobii_ctx import main`. "
            f"{CUSTOM_TOOL_RETRY_CHECKLIST}"
        )

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
        return (
            "Custom tool source must end with `if __name__ == '__main__': main(run)`. "
            f"{CUSTOM_TOOL_RETRY_CHECKLIST}"
        )
    missing_fstring_names = _find_likely_undefined_fstring_names(tree)
    if missing_fstring_names:
        names = ", ".join(missing_fstring_names)
        return f"Custom tool source may reference undefined f-string name(s): {names}. Patch all validation issues before retrying: remaining_work/next_cursor, explicit inputs, and do_not_repeat_manually when writes happen."
    invalid_datetime_refs = _invalid_datetime_timezone_refs(tree)
    if invalid_datetime_refs:
        refs = ", ".join(invalid_datetime_refs)
        return (
            f"Custom tool source uses invalid datetime reference(s): {refs}. "
            "After `from datetime import datetime`, import `timezone` separately and use `datetime.now(timezone.utc)`, "
            "or use `import datetime` with `datetime.datetime.now(datetime.timezone.utc)`."
        )
    if _has_url_regex_remainder_antipattern(tree):
        return (
            "Custom tool source appears to validate URLs with a regex path-segment match and then inspect "
            "`url[match.end():]`. The regex can consume the slug before the remainder check and reject valid URLs. "
            "Parse URLs with urllib.parse, use fullmatch/anchors, or capture and validate the path segment instead."
        )
    if _needs_actionable_result_signal(source_text) and not _has_actionable_result_signal(tree):
        return (
            "Custom tool source writes, syncs, batches, or calls tools but lacks actionable result guidance. "
            "Return next_action, verification, remaining_work, or next_cursor so the agent knows whether to verify or continue. Patch all validation issues before retrying."
        )
    if _needs_batch_progress_signal(source_text) and not _has_batch_progress_signal(tree):
        return (
            "Custom tool source accepts batch/limit params but lacks remaining-work or cursor reporting. "
            "Return remaining_work or next_cursor so the agent can resume bounded work. Patch all validation issues before retrying: explicit inputs and do_not_repeat_manually when writes happen."
        )
    if _needs_replay_prevention_signal(source_text) and not _has_replay_prevention_signal(source_text):
        return (
            "Custom tool source performs side effects but lacks manual replay prevention. "
            "Return do_not_repeat_manually=True or next_action like "
            "'Do not repeat manually; verify read-only; do not append/add/update again.' Patch all validation issues before retrying."
        )
    return None


def _normalize_pep723_fences(source_text: str) -> str:
    return re.sub(r"(?m)^(# ///)[ \t]+$", r"\1", source_text)


_MAIN_IMPORT_LINE = "from _gobii_ctx import main"
_MAIN_GUARD_LINE = "if __name__ == '__main__': main(run)"
_TRAILING_MAIN_GUARD_RE = re.compile(
    r"\n*if[ \t]+__name__[ \t]*==[ \t]*[\"']__main__[\"']:[ \t]*"
    r"(?:\n[ \t]+from[ \t]+_gobii_ctx[ \t]+import[ \t]+main[ \t]*)?"
    r"(?:\n[ \t]+main\([ \t]*run[ \t]*\)[ \t]*|main\([ \t]*run[ \t]*\)[ \t]*)\Z",
    re.MULTILINE,
)


def _leading_insert_index_after_pep723(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "# /// script":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "# ///":
            return index + 1
    return 0


def _normalize_custom_tool_source_boilerplate(source_text: str) -> str:
    normalized = _normalize_pep723_fences(source_text).replace("\r\n", "\n").replace("\r", "\n")

    if _MAIN_IMPORT_LINE not in normalized:
        lines = normalized.splitlines()
        insert_at = _leading_insert_index_after_pep723(lines)
        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        lines.insert(insert_at, _MAIN_IMPORT_LINE)
        normalized = "\n".join(lines)

    stripped = normalized.rstrip()
    if _TRAILING_MAIN_GUARD_RE.search(stripped):
        normalized = _TRAILING_MAIN_GUARD_RE.sub("\n\n" + _MAIN_GUARD_LINE, stripped)
    elif "main(run)" not in stripped:
        normalized = stripped + "\n\n" + _MAIN_GUARD_LINE
    else:
        normalized = stripped

    return normalized.rstrip() + "\n"


def validate_custom_tool_source_code(source_text: str, source_path: str) -> Optional[str]:
    return _validate_source_code(source_text, source_path)


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
def _custom_tool_sqlite_db(agent: PersistentAgent, *, current_db_path: Optional[str] = None):
    if current_db_path:
        yield current_db_path
        return

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
            "name": CREATE_CUSTOM_TOOL_NAME,
            "description": (
                "Create/update a sandboxed Python custom tool. "
                "Use for 3+ repeated steps, API/MCP fan-out, pagination, sync/import, transforms, validation/dedupe, or bulk SQLite writes; err early. "
                "If the user explicitly asks to create a custom tool, call create_custom_tool before manually doing the requested API/MCP/pagination work; do not substitute direct http_request/MCP/SQLite loops for the requested custom-tool implementation. "
                "Before the first call, verify: Exact import `from _gobii_ctx import main`; exact final line `if __name__ == '__main__': main(run)`; imports cover referenced modules, e.g. `import sqlite3` before `sqlite3.Row`; `parameters_schema.required` requires real source inputs plus destinations/filters/limits/dates; SQLite/context APIs: use `with ctx.sqlite() as db:` and pass `ctx` into helper functions that need it; never call `main.sqlite()` or `main.call_tool()` because `main` is only the entrypoint wrapper; never use `db = ctx.sqlite()`; batch/limit tools return `remaining_work`/`next_cursor`; if parameters_schema contains `limit`, `batch_limit`, or `batch_size`, source_code must literally return `remaining_work` (0 when done) or `next_cursor`; every completed write batch returns do_not_repeat_manually=true and source-code next_action text exactly like 'Do not repeat manually; verify read-only; do not append/add/update again.' If more rows remain, say to re-run the custom tool for the next batch, not to manually append/update the completed batch. "
                "Use PEP 723 for third-party deps such as `# dependencies = [\"requests[socks]\"]`; never list stdlib deps. "
                "First-call rule: the first create_custom_tool call should be complete and validator-clean. For one-shot creation pass source_path='/tools/my_tool.py' plus full source_code; do not pass only `source_path` unless you already wrote that file. Retrying is a fallback after an unexpected rejection, not the normal workflow; if rejected, fix every listed issue and retry create_custom_tool, not create_file. "
                "For tool-to-tool calls use ctx.call_tool(name, params). Write durable data to the agent SQLite DB; do not ATTACH sandbox file paths. "
                "Keep DB work inside the `with ctx.sqlite() as db:` block; after the block exits the DB is closed. Use cursor.rowcount/SELECT changes(); set db.row_factory = sqlite3.Row before SELECT/fetchall because later changes do not convert tuples and rows are not row.get(...). "
                "For UTC timestamps use datetime.now(timezone.utc), not datetime.timezone. Expose runtime params for tables, filters, URLs, limits, cursors, or destinations; do not invoke custom_* with empty params unless it intentionally reads verified state. "
                "Avoid manual MCP/tool/API loops. Slow batches should be chunkable: include `limit`/`batch_size`, filters, progress, and patch for smaller resumable batches. "
                "Every success or error return dict should include `next_action`; keep returns concise with status, summary, what changed or which outputs are ready, counts/side effects, skipped duplicates, remaining work, and verification guidance. "
                "Name ready outputs specifically (`direct_post_urls`, `scrape_ready_urls`, `rows_written`, `records_to_sync`). Validators return accepted ready-to-use values and rejected reasons; require source params like `urls`, `domains`, `candidates`, `source_table`, or `input_table`. "
                "Secrets are in os.environ; if missing, request `secret_type='env_var'`, not a domain-scoped credential. "
                "Network code needs SOCKS5 proxy support: use requests[socks]/httpx[socks], declare `dependencies = [\"requests[socks]\"]`, read ALL_PROXY/HTTP_PROXY/HTTPS_PROXY/NO_PROXY, and prefer ctx.requests_proxies() or ctx.proxy_url(); not bare `requests`/`httpx` or direct HTTPS tunneling. "
                "Filespace paths like `/tools/my_tool.py` and `/exports/report.txt` are Gobii tool args; if using create_file first, pass file_path='/tools/my_tool.py' and content=<python source>. Inside code use Path('/workspace/exports/report.txt'), not open('/exports/report.txt', ...). "
                "Latest workspace edits are synced automatically; Prefer patching the same file over creating near-duplicates. "
                "Saved tool id like `custom_my_tool`; enabled by default."
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
                        "description": (
                            "Required filespace path to the Python source file, for example `/tools/my_tool.py`. "
                            "Still required when source_code is provided."
                        ),
                    },
                    "source_code": {
                        "type": "string",
                        "description": (
                            "Optional full Python source. Preferred for one-step creation: provide this together with "
                            "source_path so the file is written and registered in one create_custom_tool call."
                        ),
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

    source_path = _normalize_custom_tool_source_path(params.get("source_path"))
    if not source_path:
        return {"status": "error", "message": "source_path must be a valid workspace path like `/tools/my_tool.py`."}

    entrypoint = _normalize_custom_tool_entrypoint(params.get("entrypoint"))
    if entrypoint is None:
        return {
            "status": "error",
            "message": "entrypoint is no longer configurable. Custom tools must use `def run(...):` and `main(run)`.",
        }

    parameters_schema = normalize_parameters_schema(params.get("parameters_schema"))
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
        source_code = _normalize_custom_tool_source_boilerplate(source_code)
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
        validation_error = _validate_source_code(source_code, source_path)
        if validation_error:
            return {"status": "error", "message": validation_error, "source_path": source_path}
        validation_error = _validate_schema_runtime_params_for_source(source_code, description, parameters_schema)
        if validation_error:
            return {"status": "error", "message": validation_error, "source_path": source_path}
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
            return {"status": "error", "message": validation_error, "source_path": source_path}
        validation_error = _validate_schema_runtime_params_for_source(source_text, description, parameters_schema)
        if validation_error:
            return {"status": "error", "message": validation_error, "source_path": source_path}

    tool, created = PersistentAgentCustomTool.objects.update_or_create(
        agent=agent,
        tool_name=tool_name,
        defaults={
            "name": display_name,
            "description": description,
            "source_path": source_path,
            "parameters_schema": parameters_schema,
            "entrypoint": entrypoint,
            "timeout_seconds": timeout_seconds,
        },
    )

    enable_result = {"enabled": [], "already_enabled": [], "evicted": [], "invalid": []}
    if enable_tool:
        from .tool_manager import enable_tools

        enable_result = enable_tools(agent, [tool.tool_name])

    from api.agent.system_skills.service import enable_and_refresh_system_skills_for_tool

    enable_and_refresh_system_skills_for_tool(agent, CREATE_CUSTOM_TOOL_NAME)

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


def execute_custom_tool(
    agent: PersistentAgent,
    tool: PersistentAgentCustomTool,
    params: Dict[str, Any],
    *,
    current_sqlite_db_path: Optional[str] = None,
) -> Dict[str, Any]:
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
    else:
        env[_EXEC_SOURCE_PATH_ENV_KEY] = posixpath.join(
            custom_tool_workspace_root_for_backend(service._backend, agent.id),
            tool.source_path.lstrip("/"),
        )

    with _custom_tool_uv_runtime_dirs(service) as runtime_env, _custom_tool_sqlite_db(
        agent,
        current_db_path=current_sqlite_db_path,
    ) as sqlite_db_path:
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
    parsed_result = _guard_custom_tool_result_replay(source_text, parsed_result)

    response = {
        "status": "ok",
        "result": parsed_result,
    }
    if isinstance(result.get("shared_sqlite_db"), dict):
        response["shared_sqlite_db"] = result["shared_sqlite_db"]
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


def format_custom_tools_state_for_prompt(agent: PersistentAgent, *, recent_limit: int = 3) -> str:
    if not is_custom_tools_available_for_agent(agent):
        return ""

    total = PersistentAgentCustomTool.objects.filter(agent=agent).count()
    enabled = PersistentAgentEnabledTool.objects.filter(
        agent=agent,
        tool_full_name__startswith=CUSTOM_TOOL_PREFIX,
    ).count()
    summary = f"Custom tools: {total} saved, {enabled} enabled."

    recent = format_recent_custom_tools_for_prompt(agent, limit=recent_limit)
    if recent:
        summary += "\nRecent custom tools:\n" + recent
    return summary


def get_custom_tools_prompt_summary(agent: PersistentAgent, *, recent_limit: int = 3) -> str:
    return format_custom_tools_state_for_prompt(agent, recent_limit=recent_limit)
