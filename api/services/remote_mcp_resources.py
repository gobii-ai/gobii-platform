import base64
import hashlib
import mimetypes
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from api.agent.files.filespace_service import FILESPACE_PERSISTENCE_ERRORS
from api.models import AgentFsNode, PersistentAgentMessageAttachment
from api.services.system_settings import get_max_file_size


RESOURCE_SCHEME = "gobii"
RESOURCE_AUTHORITY = "agents"
_MIME_TYPE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+-]*/[a-zA-Z0-9][a-zA-Z0-9!#$&^_.+-]*$")


class MCPResourceError(Exception):
    pass


@dataclass(frozen=True)
class AgentResourceReference:
    uri: str
    agent_id: uuid.UUID
    kind: str
    resource_id: uuid.UUID


@dataclass(frozen=True)
class AgentResource:
    uri: str
    name: str
    mime_type: str
    size_bytes: int
    checksum_sha256: str
    content: bytes

    def mcp_contents(self):
        try:
            if _is_text_mime_type(self.mime_type):
                return {
                    "uri": self.uri,
                    "mimeType": self.mime_type,
                    "text": self.content.decode("utf-8"),
                }
        except UnicodeDecodeError:
            pass
        return {
            "uri": self.uri,
            "mimeType": self.mime_type,
            "blob": _base64_content(self.content),
        }


def build_file_resource_uri(agent_id, node_id):
    return f"{RESOURCE_SCHEME}://{RESOURCE_AUTHORITY}/{agent_id}/files/{node_id}"


def build_attachment_resource_uri(agent_id, attachment_id):
    return f"{RESOURCE_SCHEME}://{RESOURCE_AUTHORITY}/{agent_id}/attachments/{attachment_id}"


def parse_agent_resource_uri(uri):
    if not isinstance(uri, str) or not uri:
        raise MCPResourceError("Invalid resource URI.")
    parsed = urlparse(uri)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != RESOURCE_SCHEME
        or parsed.netloc != RESOURCE_AUTHORITY
        or len(parts) != 3
        or parts[1] not in {"files", "attachments"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise MCPResourceError("Invalid resource URI.")
    try:
        agent_id = uuid.UUID(parts[0])
        resource_id = uuid.UUID(parts[2])
    except (TypeError, ValueError, AttributeError) as exc:
        raise MCPResourceError("Invalid resource URI.") from exc
    return AgentResourceReference(
        uri=uri,
        agent_id=agent_id,
        kind=parts[1],
        resource_id=resource_id,
    )


def read_agent_resource(agent, reference):
    if reference.agent_id != agent.id:
        raise MCPResourceError("Resource not found or inaccessible.")
    if reference.kind == "files":
        return _read_filespace_resource(agent, reference)
    return _read_message_attachment_resource(agent, reference)


def _read_filespace_resource(agent, reference):
    node = (
        AgentFsNode.objects.alive()
        .files()
        .filter(
            id=reference.resource_id,
            filespace__access__agent=agent,
        )
        .distinct()
        .first()
    )
    if node is None:
        raise MCPResourceError("Resource not found or inaccessible.")
    return _read_file_field(
        reference.uri,
        node.name,
        node.mime_type,
        node.size_bytes,
        node.checksum_sha256,
        node.content,
    )


def _read_message_attachment_resource(agent, reference):
    attachment = (
        PersistentAgentMessageAttachment.objects.filter(
            id=reference.resource_id,
            message__owner_agent=agent,
        )
        .select_related("filespace_node")
        .first()
    )
    if attachment is None:
        raise MCPResourceError("Resource not found or inaccessible.")

    node = attachment.filespace_node
    if node and node.content and getattr(node.content, "name", None):
        return _read_file_field(
            reference.uri,
            attachment.filename or node.name,
            attachment.content_type or node.mime_type,
            attachment.file_size or node.size_bytes,
            attachment.content_sha256 or node.checksum_sha256,
            node.content,
        )
    return _read_file_field(
        reference.uri,
        attachment.filename,
        attachment.content_type,
        attachment.file_size,
        attachment.content_sha256,
        attachment.file,
    )


def _read_file_field(uri, name, mime_type, declared_size, declared_checksum, file_field):
    if not file_field or not getattr(file_field, "name", None):
        raise MCPResourceError("Resource content is unavailable.")

    max_size = get_max_file_size()
    size_bytes = _integer_size(declared_size)
    if max_size and size_bytes is not None and size_bytes > max_size:
        raise MCPResourceError("Resource exceeds the MCP file size limit.")

    try:
        with file_field.open("rb") as handle:
            content = handle.read((max_size + 1) if max_size else -1)
    except FILESPACE_PERSISTENCE_ERRORS as exc:
        raise MCPResourceError("Resource content is unavailable.") from exc
    if max_size and len(content) > max_size:
        raise MCPResourceError("Resource exceeds the MCP file size limit.")

    checksum = hashlib.sha256(content).hexdigest()
    if declared_checksum and declared_checksum != checksum:
        raise MCPResourceError("Resource failed its integrity check.")
    return AgentResource(
        uri=uri,
        name=name or "attachment",
        mime_type=_normalize_mime_type(mime_type, name),
        size_bytes=len(content),
        checksum_sha256=checksum,
        content=content,
    )


def _normalize_mime_type(value, name):
    candidate = (value or "").strip().lower()
    if _MIME_TYPE_RE.fullmatch(candidate):
        return candidate
    guessed, _ = mimetypes.guess_type(name or "")
    if guessed and _MIME_TYPE_RE.fullmatch(guessed):
        return guessed
    return "application/octet-stream"


def _integer_size(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_text_mime_type(mime_type):
    return (
        mime_type.startswith("text/")
        or mime_type in {"application/json", "application/ld+json", "application/xml"}
        or mime_type.endswith("+json")
        or mime_type.endswith("+xml")
    )


def _base64_content(content):
    return base64.b64encode(content).decode("ascii")
