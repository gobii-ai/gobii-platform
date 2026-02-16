import codecs
import logging
import os
import tempfile
from typing import Any, Dict, Optional

from django.core.files.storage import default_storage

from markitdown import MarkItDown

from api.models import AgentFileSpaceAccess, AgentFsNode, PersistentAgent
from api.agent.core.file_handler_config import get_file_handler_llm_config
from api.agent.core.llm_utils import run_completion
from api.agent.files.filespace_service import get_or_create_default_filespace
from api.services.system_settings import get_max_file_size

logger = logging.getLogger(__name__)

DEFAULT_MAX_MARKDOWN_CHARS = 80000
DEFAULT_RESPONSE_FORMAT = "markdown"
RESPONSE_FORMAT_MARKDOWN = "markdown"
RESPONSE_FORMAT_RAW_TEXT = "raw_text"
ALLOWED_RESPONSE_FORMATS = {RESPONSE_FORMAT_MARKDOWN, RESPONSE_FORMAT_RAW_TEXT}
TEMP_FILE_PREFIX = "agent_read_"
BUFFER_SIZE = 64 * 1024

RAW_TEXT_HARD_BLOCKED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
}

RAW_TEXT_HARD_BLOCKED_MIME_TYPES = {
    "application/pdf",
    "application/msword",
}

RAW_TEXT_HARD_BLOCKED_MIME_PREFIXES = (
    "image/",
    "application/vnd.ms-",
    "application/vnd.openxmlformats-officedocument.",
    "application/vnd.oasis.opendocument.",
)


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
            cleaned = value.strip()
            # Strip $[...] wrapper if present
            if cleaned.startswith("$[") and cleaned.endswith("]"):
                cleaned = cleaned[2:-1].strip()
            return cleaned
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
        max_size = get_max_file_size()
        with default_storage.open(node.content.name, "rb") as src, open(temp_path, "wb") as dst:
            for chunk in iter(lambda: src.read(BUFFER_SIZE), b""):
                dst.write(chunk)
                total_bytes += len(chunk)
                if max_size and total_bytes > max_size:
                    raise ValueError(
                        f"File exceeds maximum allowed size while reading ({total_bytes} bytes > {max_size} bytes)."
                    )
        return temp_path
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _truncate_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    truncated = content[:max_chars]
    return f"{truncated}\n\n... (truncated to {max_chars} characters)"


def _resolve_response_format(params: Dict[str, Any]) -> str:
    raw_format = params.get("response_format", DEFAULT_RESPONSE_FORMAT)
    if not isinstance(raw_format, str):
        return ""
    return raw_format.strip().lower()


def _is_hard_blocked_for_raw_text(node: AgentFsNode) -> bool:
    mime_type = (node.mime_type or "").strip().lower()
    if mime_type:
        if mime_type.startswith(RAW_TEXT_HARD_BLOCKED_MIME_PREFIXES):
            return True
        if mime_type in RAW_TEXT_HARD_BLOCKED_MIME_TYPES:
            return True

    extension = os.path.splitext(node.name or "")[1].lower()
    return extension in RAW_TEXT_HARD_BLOCKED_EXTENSIONS


def _read_node_text(node: AgentFsNode, max_size: int | None) -> str:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    text_chunks: list[str] = []
    total_bytes = 0
    with default_storage.open(node.content.name, "rb") as src:
        for chunk in iter(lambda: src.read(BUFFER_SIZE), b""):
            if b"\x00" in chunk:
                raise UnicodeDecodeError("utf-8", chunk, 0, 1, "binary-like content with null bytes")
            total_bytes += len(chunk)
            if max_size and total_bytes > max_size:
                raise ValueError(
                    f"File exceeds maximum allowed size while reading ({total_bytes} bytes > {max_size} bytes)."
                )
            text_chunks.append(decoder.decode(chunk))
    text_chunks.append(decoder.decode(b"", final=True))
    return "".join(text_chunks)


def get_read_file_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file from the agent filesystem and return content as markdown or raw text. "
                "Prefer response_format='markdown' for PDFs, images, scanned documents, and office files "
                "(markdown mode handles OCR and richer extraction). "
                "Not for SQLite snapshots; use sqlite_batch on __tool_results, __messages, or __files instead. "
                "Markdown mode uses OCR for images to return a detailed description. "
                "Supports images, PDFs, text files, office documents, and more."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to a file in the agent filespace (accepts $[/path] variables).",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": (
                            "Optional cap on returned text length (default "
                            f"{DEFAULT_MAX_MARKDOWN_CHARS})."
                        ),
                    },
                    "response_format": {
                        "type": "string",
                        "enum": [RESPONSE_FORMAT_MARKDOWN, RESPONSE_FORMAT_RAW_TEXT],
                        "description": (
                            "Optional output format. "
                            "'markdown' (default) is recommended for PDFs/images/office docs; "
                            "'raw_text' is for plain text files."
                        ),
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

    max_size = get_max_file_size()
    if max_size and node.size_bytes and node.size_bytes > max_size:
        return {"status": "error", "message": f"File exceeds maximum allowed size ({node.size_bytes} bytes)."}

    response_format = _resolve_response_format(params)
    if response_format not in ALLOWED_RESPONSE_FORMATS:
        allowed = ", ".join(sorted(ALLOWED_RESPONSE_FORMATS))
        return {
            "status": "error",
            "message": f"Invalid response_format '{response_format}'. Allowed values: {allowed}.",
        }

    max_chars = params.get("max_chars", DEFAULT_MAX_MARKDOWN_CHARS)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_MARKDOWN_CHARS

    if response_format == RESPONSE_FORMAT_RAW_TEXT:
        if _is_hard_blocked_for_raw_text(node):
            return {
                "status": "error",
                "message": (
                    "raw_text mode is intended for plain text files. "
                    "Use response_format='markdown' for PDFs, images, and office documents."
                ),
            }
        try:
            text = _read_node_text(node, max_size=max_size)
        except UnicodeDecodeError:
            return {
                "status": "error",
                "message": (
                    "File is not valid UTF-8 text. Use response_format='markdown' for rich/binary formats."
                ),
            }
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except OSError as exc:
            logger.error("Failed to read file node %s as raw text: %s", node.id, exc)
            return {"status": "error", "message": "Failed to access the file content."}

        if max_chars > 0:
            text = _truncate_content(text, max_chars)
        return {"status": "ok", "format": RESPONSE_FORMAT_RAW_TEXT, "text": text}

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

    if max_chars > 0:
        markdown = _truncate_content(markdown, max_chars)

    return {"status": "ok", "format": RESPONSE_FORMAT_MARKDOWN, "markdown": markdown}
