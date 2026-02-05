import ipaddress
import logging
from dataclasses import dataclass
from typing import Iterable, List
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import signing
from django.urls import reverse

from api.models import AgentFileSpaceAccess, AgentFsNode, PersistentAgentMessageAttachment
from api.services.system_settings import get_max_file_size
from .filespace_service import get_or_create_default_filespace
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)


class AttachmentResolutionError(Exception):
    pass


SIGNED_FILES_DOWNLOAD_SALT = "agent-filespace-download"
SIGNED_FILES_DOWNLOAD_TTL_SECONDS = 7 * 24 * 60 * 60
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


@dataclass(frozen=True)
class ResolvedAttachment:
    node: AgentFsNode
    path: str
    filename: str
    content_type: str
    size_bytes: int


def _ext_from_name(name: str | None) -> str | None:
    if not name or "." not in name:
        return None
    return name.rsplit(".", 1)[-1].lower() or None


def _path_meta(path: str | None) -> tuple[str | None, str | None]:
    if not path:
        return None, None
    parent_path = path.rsplit("/", 1)[0] or "/"
    return parent_path, None


def _track_file_event(
    agent,
    *,
    event: AnalyticsEvent,
    node: AgentFsNode | None,
    path: str | None,
    filename: str | None,
    size_bytes: int | None,
    mime_type: str | None,
    channel: str | None,
    message_id: str | None,
    reason_code: str | None,
    user_initiated: bool | None,
) -> None:
    if agent is None or agent.user_id is None:
        return

    parent_path, _ = _path_meta(path or getattr(node, "path", None))
    extension = _ext_from_name(filename or getattr(node, "name", None))

    props: dict[str, object] = {
        "agent_id": str(agent.id),
        "channel": channel,
    }
    if node and node.filespace_id:
        props["filespace_id"] = str(node.filespace_id)
        props["node_id"] = str(node.id)
    if parent_path:
        props["parent_path"] = parent_path
    if path:
        props["path"] = path
    if extension:
        props["extension"] = extension
    if size_bytes is not None:
        props["size_bytes"] = int(size_bytes)
    if mime_type:
        props["mime_type"] = mime_type
    if message_id:
        props["message_id"] = message_id
    if reason_code:
        props["reason_code"] = reason_code
    if user_initiated is not None:
        props["user_initiated"] = bool(user_initiated)

    props = Analytics.with_org_properties(props, organization=getattr(agent, "organization", None))
    Analytics.track_event(
        user_id=str(agent.user_id),
        event=event,
        source=AnalyticsSource.AGENT,
        properties=props.copy(),
    )


def track_file_sent(
    agent,
    *,
    node: AgentFsNode | None,
    path: str | None,
    filename: str | None,
    size_bytes: int | None,
    mime_type: str | None,
    channel: str | None,
    message_id: str | None,
    user_initiated: bool | None,
) -> None:
    _track_file_event(
        agent,
        event=AnalyticsEvent.AGENT_FILE_SENT,
        node=node,
        path=path,
        filename=filename,
        size_bytes=size_bytes,
        mime_type=mime_type,
        channel=channel,
        message_id=message_id,
        reason_code=None,
        user_initiated=user_initiated,
    )


def track_file_send_failed(
    agent,
    *,
    node: AgentFsNode | None,
    path: str | None,
    filename: str | None,
    size_bytes: int | None,
    mime_type: str | None,
    channel: str | None,
    message_id: str | None,
    reason_code: str | None,
    user_initiated: bool | None,
) -> None:
    _track_file_event(
        agent,
        event=AnalyticsEvent.AGENT_FILE_SEND_FAILED,
        node=node,
        path=path,
        filename=filename,
        size_bytes=size_bytes,
        mime_type=mime_type,
        channel=channel,
        message_id=message_id,
        reason_code=reason_code,
        user_initiated=user_initiated,
    )


def track_file_unsupported(
    agent,
    *,
    node: AgentFsNode | None,
    path: str | None,
    filename: str | None,
    size_bytes: int | None,
    mime_type: str | None,
    channel: str | None,
    message_id: str | None,
    reason_code: str | None,
    user_initiated: bool | None,
) -> None:
    _track_file_event(
        agent,
        event=AnalyticsEvent.AGENT_FILE_UNSUPPORTED,
        node=node,
        path=path,
        filename=filename,
        size_bytes=size_bytes,
        mime_type=mime_type,
        channel=channel,
        message_id=message_id,
        reason_code=reason_code,
        user_initiated=user_initiated,
    )


def get_message_channel(message) -> str | None:
    if getattr(message, "conversation", None):
        return getattr(message.conversation, "channel", None)
    if getattr(message, "to_endpoint", None):
        return getattr(message.to_endpoint, "channel", None)
    if getattr(message, "from_endpoint", None):
        return getattr(message.from_endpoint, "channel", None)
    return None


def normalize_attachment_paths(raw_paths: object) -> List[str]:
    if raw_paths is None:
        return []
    if isinstance(raw_paths, str):
        paths = [raw_paths]
    elif isinstance(raw_paths, (list, tuple)):
        paths = list(raw_paths)
    else:
        raise AttachmentResolutionError("Attachments must be a list of filespace paths.")

    normalized: List[str] = []
    seen: set[str] = set()
    for item in paths:
        if not isinstance(item, str):
            raise AttachmentResolutionError("Attachment paths must be strings.")
        value = item.strip()
        # Strip $[...] wrapper if present
        if value.startswith("$[") and value.endswith("]"):
            value = value[2:-1].strip()
        if not value:
            raise AttachmentResolutionError("Attachment path cannot be empty.")
        if not value.startswith("/"):
            value = f"/{value}"
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def resolve_filespace_attachments(agent, raw_paths: object) -> List[ResolvedAttachment]:
    try:
        paths = normalize_attachment_paths(raw_paths)
    except AttachmentResolutionError as exc:
        track_file_send_failed(
            agent,
            node=None,
            path=None,
            filename=None,
            size_bytes=None,
            mime_type=None,
            channel=None,
            message_id=None,
            reason_code="validation_failed",
            user_initiated=True,
        )
        raise
    if not paths:
        return []

    filespace = get_or_create_default_filespace(agent)
    if not AgentFileSpaceAccess.objects.filter(agent=agent, filespace=filespace).exists():
        track_file_send_failed(
            agent,
            node=None,
            path=None,
            filename=None,
            size_bytes=None,
            mime_type=None,
            channel=None,
            message_id=None,
            reason_code="no_access",
            user_initiated=True,
        )
        raise AttachmentResolutionError("Agent lacks access to the default filespace.")

    nodes = (
        AgentFsNode.objects
        .filter(
            filespace=filespace,
            path__in=paths,
            node_type=AgentFsNode.NodeType.FILE,
            is_deleted=False,
        )
    )
    nodes_by_path = {node.path: node for node in nodes}
    missing = [path for path in paths if path not in nodes_by_path]
    if missing:
        track_file_send_failed(
            agent,
            node=None,
            path=missing[0],
            filename=None,
            size_bytes=None,
            mime_type=None,
            channel=None,
            message_id=None,
            reason_code="not_found",
            user_initiated=True,
        )
        raise AttachmentResolutionError(f"Attachment not found in default filespace: {missing[0]}")

    max_bytes = get_max_file_size()
    resolved: List[ResolvedAttachment] = []
    for path in paths:
        node = nodes_by_path[path]
        file_field = getattr(node, "content", None)
        if not file_field or not getattr(file_field, "name", None):
            track_file_send_failed(
                agent,
                node=node,
                path=path,
                filename=node.name,
                size_bytes=None,
                mime_type=node.mime_type or None,
                channel=None,
                message_id=None,
                reason_code="missing_blob",
                user_initiated=True,
            )
            raise AttachmentResolutionError(f"Attachment has no stored content: {path}")

        size_bytes = node.size_bytes
        if size_bytes is None and hasattr(file_field, "size"):
            try:
                size_bytes = int(file_field.size)
            except (TypeError, ValueError):
                size_bytes = None
        if max_bytes and size_bytes and int(size_bytes) > int(max_bytes):
            track_file_unsupported(
                agent,
                node=node,
                path=path,
                filename=node.name,
                size_bytes=int(size_bytes),
                mime_type=node.mime_type or None,
                channel=None,
                message_id=None,
                reason_code="too_large",
                user_initiated=True,
            )
            raise AttachmentResolutionError(
                f"Attachment exceeds max size of {max_bytes} bytes: {path}"
            )

        filename = node.name or "attachment"
        content_type = node.mime_type or "application/octet-stream"
        resolved.append(
            ResolvedAttachment(
                node=node,
                path=node.path,
                filename=filename,
                content_type=content_type,
                size_bytes=int(size_bytes or 0),
            )
        )
    return resolved


def create_message_attachments(message, attachments: Iterable[ResolvedAttachment]) -> None:
    agent = getattr(message, "owner_agent", None)
    channel = get_message_channel(message)
    user_initiated = bool(getattr(agent, "user_id", None)) if agent else None
    for att in attachments:
        try:
            size_bytes = int(att.size_bytes or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        PersistentAgentMessageAttachment.objects.create(
            message=message,
            file="",
            content_type=att.content_type,
            file_size=size_bytes,
            filename=att.filename,
            filespace_node=att.node,
        )
        track_file_sent(
            agent,
            node=att.node,
            path=att.path,
            filename=att.filename,
            size_bytes=size_bytes,
            mime_type=att.content_type,
            channel=channel,
            message_id=str(getattr(message, "id", "")) if getattr(message, "id", None) else None,
            user_initiated=user_initiated,
        )


def build_filespace_download_url(agent_id, node_id) -> str:
    current_site = Site.objects.get_current()
    base = f"https://{current_site.domain}"
    path = reverse("console_agent_fs_download", kwargs={"agent_id": agent_id})
    query = urlencode({"node_id": node_id})
    return f"{base}{path}?{query}"


def build_signed_filespace_download_url(agent_id, node_id) -> str:
    """Build a signed URL for downloading a file from agent filespace.

    Returns a full absolute URL using the Django Sites framework.
    The Site domain must be configured correctly for each environment:
    - Development: localhost:8000
    - Production: your-domain.com
    - Custom deployments: their configured domain
    """
    token = signing.dumps(
        {"agent_id": str(agent_id), "node_id": str(node_id)},
        salt=SIGNED_FILES_DOWNLOAD_SALT,
        compress=True,
    )
    path = reverse("signed_agent_fs_download", kwargs={"token": token})
    base = _resolve_signed_download_base_url()
    return f"{base}{path}"


def _resolve_signed_download_base_url() -> str:
    current_site = Site.objects.get_current()
    domain, explicit_scheme = _extract_domain_and_scheme(getattr(current_site, "domain", ""))
    if not domain:
        domain = "localhost:8000"

    scheme = (
        explicit_scheme
        or _scheme_from_public_site_url(domain)
        or _infer_scheme_for_domain(domain)
    )
    return f"{scheme}://{domain}"


def _extract_domain_and_scheme(raw_domain: str) -> tuple[str, str | None]:
    value = (raw_domain or "").strip()
    if not value:
        return "", None
    if "://" not in value:
        return value, None

    parsed = urlparse(value)
    domain = (parsed.netloc or parsed.path or "").strip()
    scheme = (parsed.scheme or "").strip().lower() or None
    return domain, scheme


def _hostname_from_domain(domain: str) -> str:
    value = (domain or "").strip()
    if not value:
        return ""

    if value.startswith("["):
        end = value.find("]")
        if end > 1:
            return value[1:end].lower()

    # Preserve non-bracketed IPv6 literals by avoiding split when multiple colons are present.
    if value.count(":") == 1:
        return value.split(":", 1)[0].lower()
    return value.lower()


def _is_local_or_private_host(hostname: str) -> bool:
    host = (hostname or "").strip().strip("[]").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True

    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(".local")

    return bool(
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip in _CGNAT_NETWORK
    )


def _scheme_from_public_site_url(domain: str) -> str | None:
    public_site_url = (getattr(settings, "PUBLIC_SITE_URL", "") or "").strip()
    if not public_site_url:
        return None

    parsed = urlparse(public_site_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return None

    public_host = (parsed.hostname or "").lower()
    domain_host = _hostname_from_domain(domain)
    if public_host in _LOCAL_HOSTNAMES and not _is_local_or_private_host(domain_host):
        return None
    return scheme


def _infer_scheme_for_domain(domain: str) -> str:
    domain_host = _hostname_from_domain(domain)
    if _is_local_or_private_host(domain_host):
        return "http"
    return "https"


def load_signed_filespace_download_payload(token: str) -> dict | None:
    try:
        payload = signing.loads(
            token,
            salt=SIGNED_FILES_DOWNLOAD_SALT,
            max_age=SIGNED_FILES_DOWNLOAD_TTL_SECONDS,
        )
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    return payload
