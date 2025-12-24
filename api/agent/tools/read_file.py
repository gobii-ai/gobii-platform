import logging
import os
import tempfile
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.files.storage import default_storage

from markitdown import MarkItDown

from api.models import AgentFileSpaceAccess, AgentFsNode, PersistentAgent
from api.agent.core.file_handler_config import get_file_handler_llm_config
from api.agent.core.llm_utils import run_completion
from api.agent.files.filespace_service import get_or_create_default_filespace

logger = logging.getLogger(__name__)

DEFAULT_MAX_MARKDOWN_CHARS = 80000
TEMP_FILE_PREFIX = "agent_read_"
BUFFER_SIZE = 64 * 1024


class _MarkItDownChatCompletions:
    def __init__(self, model: str, params: Dict[str, Any]):
        self._model = model
        self._params = params

    def create(self, *, model: Optional[str] = None, messages: Optional[list[dict[str, Any]]] = None, **kwargs: Any):
        return run_completion(
            model=model or self._model,
            messages=messages or [],
            params=self._params,
            drop_params=True,
            **kwargs,
        )


class _MarkItDownChat:
    def __init__(self, model: str, params: Dict[str, Any]):
        self.completions = _MarkItDownChatCompletions(model, params)


class MarkItDownLitellmClient:
    def __init__(self, model: str, params: Dict[str, Any]):
        self.chat = _MarkItDownChat(model, params)


def _resolve_path(params: Dict[str, Any]) -> Optional[str]:
    for key in ("path", "file_path", "filename"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _get_filespace(agent: PersistentAgent):
    try:
        return get_or_create_default_filespace(agent)
    except Exception as exc:
        logger.error("Failed to resolve default filespace for agent %s: %s", agent.id, exc)
        return None


def _agent_has_access(agent: PersistentAgent, filespace_id) -> bool:
    return AgentFileSpaceAccess.objects.filter(agent=agent, filespace_id=filespace_id).exists()


def _copy_node_to_tempfile(node: AgentFsNode) -> str:
    suffix = os.path.splitext(node.name)[1] if "." in node.name else ""
    fd, temp_path = tempfile.mkstemp(prefix=TEMP_FILE_PREFIX, suffix=suffix)
    os.close(fd)

    try:
        total_bytes = 0
        with default_storage.open(node.content.name, "rb") as src, open(temp_path, "wb") as dst:
            for chunk in iter(lambda: src.read(BUFFER_SIZE), b""):
                dst.write(chunk)
                total_bytes += len(chunk)
                max_size = getattr(settings, "MAX_FILE_SIZE", None)
                if max_size and total_bytes > max_size:
                    raise ValueError(
                        f"File exceeds maximum allowed size while reading ({total_bytes} bytes > {max_size} bytes)."
                    )
        return temp_path
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _truncate_markdown(markdown: str, max_chars: int) -> str:
    if len(markdown) <= max_chars:
        return markdown
    truncated = markdown[:max_chars]
    return f"{truncated}\n\n... (truncated to {max_chars} characters)"


def get_read_file_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file from the agent filesystem and return markdown. "
                "Can read images with OCR. "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to a file in the agent filespace."},
                    "max_chars": {
                        "type": "integer",
                        "description": f"Optional cap on the markdown length (default {DEFAULT_MAX_MARKDOWN_CHARS}).",
                    },
                },
                "required": ["path"],
            },
        },
    }


def execute_read_file(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve_path(params)
    if not path:
        return {"status": "error", "message": "Missing required parameter: path"}

    filespace = _get_filespace(agent)
    if not filespace:
        return {"status": "error", "message": "No filespace configured for this agent."}

    if not _agent_has_access(agent, filespace.id):
        return {"status": "error", "message": "Agent lacks access to the filespace."}

    try:
        node = (
            AgentFsNode.objects
            .filter(filespace=filespace, path=path, is_deleted=False)
            .first()
        )
        if not node:
            return {"status": "error", "message": f"File not found: {path}"}
        if node.node_type != AgentFsNode.NodeType.FILE:
            return {"status": "error", "message": f"Path is a directory: {path}"}
    except Exception as exc:
        logger.error("Failed to lookup file node for %s: %s", path, exc)
        return {"status": "error", "message": "Failed to locate the file in the filespace."}

    if not node.content or not getattr(node.content, "name", None):
        return {"status": "error", "message": "File has no stored content."}

    max_size = getattr(settings, "MAX_FILE_SIZE", None)
    if max_size and node.size_bytes and node.size_bytes > max_size:
        return {"status": "error", "message": f"File exceeds maximum allowed size ({node.size_bytes} bytes)."}

    try:
        temp_path = _copy_node_to_tempfile(node)
    except Exception as exc:
        logger.error("Failed to copy file node %s to temp file: %s", node.id, exc)
        return {"status": "error", "message": "Failed to access the file content."}

    try:
        llm_config = get_file_handler_llm_config()
        md_kwargs: Dict[str, Any] = {}
        if llm_config and llm_config.supports_vision:
            md_kwargs["llm_client"] = MarkItDownLitellmClient(llm_config.model, llm_config.params)
            md_kwargs["llm_model"] = llm_config.model
        converter = MarkItDown(**md_kwargs)
        result = converter.convert(temp_path)
        markdown = result.markdown or ""
    except Exception as exc:
        logger.exception("read_file conversion failed for %s: %s", path, exc)
        return {"status": "error", "message": "Failed to convert the file to markdown."}
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            logger.warning("Failed to clean up temp file %s", temp_path)

    max_chars = params.get("max_chars", DEFAULT_MAX_MARKDOWN_CHARS)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_MARKDOWN_CHARS
    if max_chars > 0:
        markdown = _truncate_markdown(markdown, max_chars)

    return {"status": "ok", "markdown": markdown}
