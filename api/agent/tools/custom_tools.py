import base64
import contextlib
import json
import logging
import posixpath
import re
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
ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SOURCE_ENV_KEY = "SANDBOX_CUSTOM_TOOL_SOURCE_B64"
_PARAMS_ENV_KEY = "SANDBOX_CUSTOM_TOOL_PARAMS_B64"
_ENTRYPOINT_ENV_KEY = "SANDBOX_CUSTOM_TOOL_ENTRYPOINT"
_BRIDGE_URL_ENV_KEY = "SANDBOX_CUSTOM_TOOL_BRIDGE_URL"
_TOKEN_ENV_KEY = "SANDBOX_CUSTOM_TOOL_TOKEN"
_TOOL_NAME_ENV_KEY = "SANDBOX_CUSTOM_TOOL_NAME"
_SOURCE_PATH_ENV_KEY = "SANDBOX_CUSTOM_TOOL_SOURCE_PATH"
_SQLITE_DB_PATH_ENV_KEY = "SANDBOX_CUSTOM_TOOL_SQLITE_DB_PATH"

CUSTOM_TOOL_BOOTSTRAP_COMMAND = textwrap.dedent(
    f"""
    python - <<'PY'
    import base64
    import inspect
    import json
    import os
    import sqlite3
    import sys
    import traceback
    import urllib.error
    import urllib.request

    RESULT_MARKER = {CUSTOM_TOOL_RESULT_MARKER!r}


    def _decode_text_env(key):
        raw = os.environ.get(key, "")
        if not raw:
            return ""
        return base64.b64decode(raw.encode("utf-8")).decode("utf-8")


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

        def call_tool(self, tool_name, params=None, **kwargs):
            payload = {{
                "tool_name": tool_name,
                "params": params if params is not None else kwargs,
            }}
            body = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                self.bridge_url,
                data=body,
                headers={{
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {{self.token}}",
                }},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    raw = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                raise RuntimeError(f"Tool bridge returned HTTP {{exc.code}}: {{detail[:500]}}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Tool bridge request failed: {{exc}}") from exc
            try:
                return json.loads(raw or "{{}}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Tool bridge returned invalid JSON: {{raw[:500]}}") from exc

        def log(self, *parts):
            print(*parts, file=sys.stderr)


    def _json_safe(value):
        return json.loads(json.dumps(value, default=str))


    def _checkpoint_sqlite(sqlite_db_path):
        if not sqlite_db_path or not os.path.exists(sqlite_db_path):
            return
        try:
            conn = sqlite3.connect(sqlite_db_path)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            print(f"SQLite checkpoint failed: {{exc}}", file=sys.stderr)


    def _invoke(entry, params, ctx):
        signature = inspect.signature(entry)
        positional = [
            param
            for param in signature.parameters.values()
            if param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        if len(positional) >= 2:
            first = positional[0].name.lower()
            if first in ("ctx", "context"):
                return entry(ctx, params)
            return entry(params, ctx)
        if len(positional) == 1:
            first = positional[0].name.lower()
            if first in ("ctx", "context"):
                return entry(ctx)
            return entry(params)
        return entry()


    def _main():
        source = _decode_text_env({_SOURCE_ENV_KEY!r})
        params = _decode_json_env({_PARAMS_ENV_KEY!r}, {{}})
        entrypoint_name = os.environ.get({_ENTRYPOINT_ENV_KEY!r}, "run")
        source_path = os.environ.get({_SOURCE_PATH_ENV_KEY!r}, os.environ.get({_TOOL_NAME_ENV_KEY!r}, "custom_tool.py"))

        namespace = {{
            "__name__": "__custom_tool__",
            "__file__": source_path,
        }}
        exec(compile(source, source_path, "exec"), namespace)

        entry = namespace.get(entrypoint_name)
        if not callable(entry):
            raise RuntimeError(f"Custom tool entrypoint '{{entrypoint_name}}' is not callable")

        ctx = ToolContext()
        if ctx.sqlite_db_path:
            os.makedirs(os.path.dirname(ctx.sqlite_db_path), exist_ok=True)
        result = _invoke(entry, params, ctx)
        _checkpoint_sqlite(ctx.sqlite_db_path)
        print(RESULT_MARKER + json.dumps({{"result": _json_safe(result)}}, default=str))


    try:
        _main()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise
    PY
    """
).strip()


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


def _normalize_entrypoint(value: Any) -> Optional[str]:
    if value in (None, ""):
        return "run"
    if not isinstance(value, str):
        return None
    entrypoint = value.strip()
    if not entrypoint or not ENTRYPOINT_RE.match(entrypoint):
        return None
    return entrypoint


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


def _validate_source_code(source_text: str, source_path: str) -> Optional[str]:
    source_bytes = source_text.encode("utf-8")
    if len(source_bytes) > MAX_CUSTOM_TOOL_SOURCE_BYTES:
        return f"Custom tool source must be {MAX_CUSTOM_TOOL_SOURCE_BYTES} bytes or smaller."
    try:
        compile(source_text, source_path, "exec")
    except SyntaxError as exc:
        return f"Custom tool source has a syntax error: {exc}"
    return None


def _encode_env_json(value: Dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(value).encode("utf-8")).decode("ascii")


def _encode_env_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


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


def get_create_custom_tool_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_custom_tool",
            "description": (
                "Create or update a sandboxed Python custom tool for this agent. "
                "Use custom tools for bulk data processing, repetitive deterministic work, "
                "or reusable multi-step tool orchestration. "
                "The source file must be self-contained and define `run(params, ctx)` "
                "(or `run(ctx, params)`) returning JSON-serializable data. "
                "Inside the tool, use `ctx.call_tool(name, params)` to invoke other agent tools, "
                "including MCP tools and other `custom_*` tools. "
                "`ctx.sqlite_db_path` points at the agent's embedded SQLite file for direct `sqlite3` reads/writes. "
                "Agent filespace contents are synced into the sandbox before execution. "
                "For file-heavy work, shell out with Python subprocess using sandbox tools like "
                "`rg`, `fd`, `jq`, `sqlite3`, `sed`, `awk`, `file`, `tar`, `unzip`, `fzf`, `yq`, and `git` "
                "instead of iterating through `read_file`. "
                "Example flow: `fd`/`rg --files` to shortlist files -> `rg -n` or `sed -n` to inspect exact regions -> "
                "`jq`/`awk`/`sqlite3` to normalize and persist results. "
                "Provide `source_code` to write the file now, or point at an existing filespace `.py` file. "
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
                        "description": "Filespace path to the Python source file, for example `/tools/my_tool.py`.",
                    },
                    "source_code": {
                        "type": "string",
                        "description": "Optional full Python source. When provided, it overwrites `source_path` before registration.",
                    },
                    "parameters_schema": {
                        "type": "object",
                        "description": "JSON schema object describing the tool input parameters.",
                    },
                    "entrypoint": {
                        "type": "string",
                        "description": "Optional Python function name to call. Defaults to `run`.",
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
        return {"status": "error", "message": "source_path must be a valid filespace path."}
    if not source_path.endswith(".py"):
        return {"status": "error", "message": "source_path must point to a `.py` file."}

    entrypoint = _normalize_entrypoint(params.get("entrypoint"))
    if not entrypoint:
        return {"status": "error", "message": "entrypoint must be a valid Python identifier."}

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
            "entrypoint": entrypoint,
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
        "entrypoint": tool.entrypoint,
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
        _SOURCE_ENV_KEY: _encode_env_text(source_text),
        _PARAMS_ENV_KEY: _encode_env_json(params or {}),
        _ENTRYPOINT_ENV_KEY: tool.entrypoint,
        _BRIDGE_URL_ENV_KEY: bridge_url,
        _TOKEN_ENV_KEY: build_custom_tool_bridge_token(agent, tool, parent_step_id=parent_step_id),
        _TOOL_NAME_ENV_KEY: tool.tool_name,
        _SOURCE_PATH_ENV_KEY: tool.source_path,
    }

    try:
        service = SandboxComputeService()
    except SandboxComputeUnavailable as exc:
        return {"status": "error", "message": str(exc)}

    with _custom_tool_sqlite_db(agent) as sqlite_db_path:
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
            "message": "Custom tool did not return a result. Ensure the entrypoint returns JSON-serializable data.",
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
        "Discoverable via search_tools; share the enabled-tool limit. "
        "Use custom tools when you need programmatic logic (loops, conditionals, error handling, data transforms), "
        "direct embedded SQLite access, bulk data processing, repetitive deterministic work, "
        "or reusable orchestration around tool calls. "
        "For simple sequential tool use, call tools directly instead. "
        "Dev loop: create_custom_tool(source_code=...) -> invoke the custom_* tool -> inspect result/error -> "
        "file_str_replace to patch source -> re-invoke. "
        "Source must define run(params, ctx) returning JSON-serializable data. "
        "ctx.call_tool(name, params) can call any available agent tool, including MCP, builtins, and other custom_* tools, "
        "and returns the result dict. "
        "ctx.sqlite_db_path points at the embedded SQLite DB for direct sqlite3 reads/writes. "
        "Agent filespace contents are synced into the sandbox before each run. "
        "The sandbox includes rg, fd, jq, sqlite3, sed, awk, file, tar, unzip, fzf, yq, and git; "
        "use them directly via subprocess for file-heavy work instead of repeated read_file calls. "
        "Micro trajectory 1: fd/rg --files to inventory candidate files -> rg -n or sed -n to inspect exact matches -> "
        "return a compact summary or write a derived artifact. "
        "Micro trajectory 2: rg -l or fd to collect many JSON/CSV/log files -> jq/awk to normalize rows -> "
        "load them into sqlite3 or write an export under /exports/. "
        "Example: result = ctx.call_tool('http_request', {'method': 'GET', 'url': url}). "
        "Once stable, save the workflow as a skill referencing the canonical custom_* tool id."
    )

    recent = format_recent_custom_tools_for_prompt(agent, limit=recent_limit)
    if recent:
        summary += "\nRecent custom tools:\n" + recent
    return summary
