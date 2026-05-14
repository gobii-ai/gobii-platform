import base64
import binascii
import copy
import json
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Q

from rest_framework.exceptions import ValidationError as DRFValidationError

from api.agent.comms.message_service import inject_internal_web_message
from api.agent.files.attachment_helpers import (
    AttachmentResolutionError,
    create_message_attachments,
    resolve_filespace_attachments,
)
from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.models import (
    AgentFsNode,
    AgentPeerLink,
    ApiKey,
    CommsChannel,
    PersistentAgent,
    build_web_user_address,
)
from api.serializers import PersistentAgentSerializer
from console.agent_chat.timeline import (
    DEFAULT_PAGE_SIZE as TIMELINE_DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE as TIMELINE_MAX_PAGE_SIZE,
    fetch_timeline_window,
    serialize_processing_snapshot,
)
from pages.account_info_cache import invalidate_account_info_cache
from util.trial_enforcement import can_user_use_personal_agents_and_api


MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {
    "name": "gobii",
    "title": "Gobii",
    "version": "2.19.0",
}


class MCPToolError(Exception):
    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data


def _agent_schema(description):
    return {
        "type": "string",
        "format": "uuid",
        "description": description,
    }


TOOL_DEFINITIONS = [
    {
        "name": "gobii_list_agents",
        "title": "List Gobii Agents",
        "description": "List persistent Gobii agents accessible to the API key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "gobii_get_agent",
        "title": "Get Gobii Agent",
        "description": "Retrieve details for one persistent Gobii agent.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": _agent_schema("Persistent agent UUID.")},
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "gobii_create_agent",
        "title": "Create Gobii Agent",
        "description": "Create a persistent Gobii agent using the same provisioning path as the public Agent API.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Optional display name. Gobii generates one when omitted."},
                "charter": {"type": "string", "description": "Instructions describing the agent's job."},
                "schedule": {"type": ["string", "null"], "description": "Optional cron-like schedule, @daily, or @every interval."},
                "is_active": {"type": "boolean", "default": True},
                "whitelist_policy": {
                    "type": "string",
                    "enum": ["default", "manual"],
                    "description": "Contact allowlist policy.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {"destructiveHint": False},
    },
    {
        "name": "gobii_update_agent",
        "title": "Update Gobii Agent",
        "description": "Update mutable persistent agent settings such as name, charter, schedule, active state, or whitelist policy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Persistent agent UUID."),
                "name": {"type": "string"},
                "charter": {"type": "string"},
                "schedule": {"type": ["string", "null"]},
                "is_active": {"type": "boolean"},
                "whitelist_policy": {"type": "string", "enum": ["default", "manual"]},
                "proactive_opt_in": {"type": "boolean"},
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gobii_archive_agent",
        "title": "Archive Gobii Agent",
        "description": "Soft-delete a persistent Gobii agent using Gobii's normal archive/delete behavior.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": _agent_schema("Persistent agent UUID.")},
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "annotations": {"destructiveHint": True},
    },
    {
        "name": "gobii_list_agent_links",
        "title": "List Agent Links",
        "description": "List peer-agent links for accessible agents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Optional agent UUID to filter links."),
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "gobii_link_agents",
        "title": "Link Gobii Agents",
        "description": "Create or enable a peer-agent link between two accessible agents so they can coordinate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("First persistent agent UUID."),
                "peer_agent_id": _agent_schema("Second persistent agent UUID."),
                "messages_per_window": {"type": "integer", "minimum": 1, "maximum": 500, "default": 30},
                "window_hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 6},
            },
            "required": ["agent_id", "peer_agent_id"],
            "additionalProperties": False,
        },
        "annotations": {"destructiveHint": False},
    },
    {
        "name": "gobii_unlink_agents",
        "title": "Unlink Gobii Agents",
        "description": "Remove a peer-agent link while preserving historical peer conversation messages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "peer_link_id": {"type": "string", "format": "uuid", "description": "Existing peer link UUID."},
                "agent_id": _agent_schema("First persistent agent UUID when peer_link_id is omitted."),
                "peer_agent_id": _agent_schema("Second persistent agent UUID when peer_link_id is omitted."),
            },
            "additionalProperties": False,
        },
        "annotations": {"destructiveHint": True},
    },
    {
        "name": "gobii_send_agent_message",
        "title": "Send Agent Message",
        "description": "Send a web-chat message to a persistent agent and optionally attach existing filespace files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Persistent agent UUID."),
                "body": {"type": "string", "description": "Message body sent to the agent."},
                "attachment_file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional filespace paths such as /uploads/report.pdf to attach to the message.",
                },
                "trigger_processing": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to queue the agent to process the inbound message.",
                },
            },
            "required": ["agent_id", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "gobii_get_agent_timeline",
        "title": "Get Agent Timeline",
        "description": "Fetch recent chat, task, thinking, and processing events for a persistent agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Persistent agent UUID."),
                "cursor": {"type": ["string", "null"], "description": "Cursor from a previous timeline result."},
                "direction": {"type": "string", "enum": ["initial", "older", "newer"], "default": "initial"},
                "limit": {"type": "integer", "minimum": 1, "maximum": TIMELINE_MAX_PAGE_SIZE, "default": TIMELINE_DEFAULT_PAGE_SIZE},
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "gobii_list_agent_files",
        "title": "List Agent Files",
        "description": "List files and folders in an agent's default filespace.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": _agent_schema("Persistent agent UUID.")},
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "gobii_upload_agent_file",
        "title": "Upload Agent File",
        "description": "Upload a small base64-encoded file into an agent's filespace for later use or message attachment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Persistent agent UUID."),
                "path": {"type": "string", "description": "Filespace path where the file should be stored, e.g. /uploads/report.txt."},
                "content_base64": {"type": "string", "description": "Base64-encoded file content."},
                "mime_type": {"type": "string", "default": "application/octet-stream"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["agent_id", "path", "content_base64"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOL_DEFINITIONS}


def list_tools():
    return copy.deepcopy(TOOL_DEFINITIONS)


def make_tool_result(data, *, is_error=False):
    safe_data = _json_safe(data)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(safe_data, cls=DjangoJSONEncoder, indent=2),
            }
        ],
        "structuredContent": safe_data,
        "isError": bool(is_error),
    }


def call_tool(request, name, arguments):
    if not isinstance(arguments, dict):
        raise MCPToolError("Tool arguments must be an object.")
    if name not in TOOL_NAMES:
        raise MCPToolError(f"Unknown tool: {name}")

    handler = {
        "gobii_list_agents": _tool_list_agents,
        "gobii_get_agent": _tool_get_agent,
        "gobii_create_agent": _tool_create_agent,
        "gobii_update_agent": _tool_update_agent,
        "gobii_archive_agent": _tool_archive_agent,
        "gobii_list_agent_links": _tool_list_agent_links,
        "gobii_link_agents": _tool_link_agents,
        "gobii_unlink_agents": _tool_unlink_agents,
        "gobii_send_agent_message": _tool_send_agent_message,
        "gobii_get_agent_timeline": _tool_get_agent_timeline,
        "gobii_list_agent_files": _tool_list_agent_files,
        "gobii_upload_agent_file": _tool_upload_agent_file,
    }[name]
    return handler(request, arguments)


def _tool_list_agents(request, arguments):
    page_size = _bounded_int(arguments.get("page_size", 20), "page_size", minimum=1, maximum=100)
    page = _bounded_int(arguments.get("page", 1), "page", minimum=1, maximum=100000)
    queryset = _agent_queryset(request)
    total = queryset.count()
    offset = (page - 1) * page_size
    agents = list(queryset[offset:offset + page_size])
    return {
        "agents": [_serialize_agent(agent) for agent in agents],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": offset + page_size < total,
    }


def _tool_get_agent(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    return {"agent": _serialize_agent(agent)}


def _tool_create_agent(request, arguments):
    allowed_fields = {"name", "charter", "schedule", "is_active", "whitelist_policy"}
    payload = {key: arguments[key] for key in allowed_fields if key in arguments}
    serializer = PersistentAgentSerializer(
        data=payload,
        context={"request": request, "organization": _request_organization(request)},
    )
    try:
        serializer.is_valid(raise_exception=True)
        agent = serializer.save()
    except (DRFValidationError, DjangoValidationError) as exc:
        raise MCPToolError("Agent creation failed.", _format_validation_error(exc)) from exc

    return {"agent": _serialize_agent(agent)}


def _tool_update_agent(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    allowed_fields = {"name", "charter", "schedule", "is_active", "whitelist_policy", "proactive_opt_in"}
    payload = {key: arguments[key] for key in allowed_fields if key in arguments}
    if not payload:
        raise MCPToolError("At least one mutable field is required.")

    serializer = PersistentAgentSerializer(
        agent,
        data=payload,
        partial=True,
        context={"request": request, "organization": _request_organization(request)},
    )
    try:
        serializer.is_valid(raise_exception=True)
        agent = serializer.save()
    except (DRFValidationError, DjangoValidationError) as exc:
        raise MCPToolError("Agent update failed.", _format_validation_error(exc)) from exc

    return {"agent": _serialize_agent(agent)}


def _tool_archive_agent(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    changed = agent.soft_delete()
    invalidate_account_info_cache(request.user.id)
    return {
        "status": "archived",
        "changed": changed,
        "agent": _serialize_agent(agent),
    }


def _tool_list_agent_links(request, arguments):
    accessible = _agent_queryset(request).only("id")
    links = (
        AgentPeerLink.objects.filter(Q(agent_a__in=accessible) | Q(agent_b__in=accessible))
        .select_related("agent_a", "agent_b", "created_by")
        .distinct()
        .order_by("-created_at")
    )
    agent_id = arguments.get("agent_id")
    if agent_id:
        agent = _get_agent(request, agent_id)
        links = links.filter(Q(agent_a=agent) | Q(agent_b=agent))

    return {"links": [_serialize_peer_link(link) for link in links]}


def _tool_link_agents(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    peer_agent = _get_agent(request, arguments.get("peer_agent_id"))
    if agent.id == peer_agent.id:
        raise MCPToolError("Cannot link an agent to itself.")

    messages_per_window = _bounded_int(
        arguments.get("messages_per_window", 30),
        "messages_per_window",
        minimum=1,
        maximum=500,
    )
    window_hours = _bounded_int(arguments.get("window_hours", 6), "window_hours", minimum=1, maximum=168)
    pair_key = AgentPeerLink.build_pair_key(agent.id, peer_agent.id)
    link = AgentPeerLink.objects.filter(pair_key=pair_key).first()
    created = False
    if link is None:
        link = AgentPeerLink(
            agent_a=agent,
            agent_b=peer_agent,
            created_by=request.user,
            messages_per_window=messages_per_window,
            window_hours=window_hours,
            is_enabled=True,
        )
        created = True
    else:
        link.messages_per_window = messages_per_window
        link.window_hours = window_hours
        link.is_enabled = True

    try:
        link.save()
    except (DjangoValidationError, IntegrityError) as exc:
        raise MCPToolError("Agent link could not be saved.", _format_validation_error(exc)) from exc

    return {"link": _serialize_peer_link(link), "created": created}


def _tool_unlink_agents(request, arguments):
    link = _resolve_peer_link(request, arguments)
    payload = _serialize_peer_link(link)
    link.remove_preserving_history()
    return {"status": "unlinked", "link": payload}


def _tool_send_agent_message(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    body = _required_string(arguments, "body", allow_blank=False)
    trigger_processing = _optional_bool(arguments.get("trigger_processing", True), "trigger_processing")
    attachment_paths = arguments.get("attachment_file_paths") or []
    if not isinstance(attachment_paths, list):
        raise MCPToolError("attachment_file_paths must be an array of filespace paths.")

    sender_address = build_web_user_address(user_id=request.user.id, agent_id=agent.id)
    if not agent.is_sender_whitelisted(CommsChannel.WEB, sender_address):
        raise MCPToolError("Authenticated user is not allowed to message this agent.")

    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        raise MCPToolError(str(exc)) from exc

    with transaction.atomic():
        message, conversation = inject_internal_web_message(
            agent.id,
            body,
            sender_user_id=request.user.id,
            attachments=[],
            trigger_processing=False,
        )
        create_message_attachments(message, resolved_attachments)
        if trigger_processing:
            from api.agent.tasks import process_agent_events_task

            transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.id)))

    return {
        "status": "queued" if trigger_processing else "stored",
        "message": _serialize_message(message),
        "conversation_id": str(conversation.id),
        "attachment_count": len(resolved_attachments),
    }


def _tool_get_agent_timeline(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    direction = arguments.get("direction") or "initial"
    if direction not in {"initial", "older", "newer"}:
        raise MCPToolError("direction must be one of initial, older, or newer.")
    limit = _bounded_int(
        arguments.get("limit", TIMELINE_DEFAULT_PAGE_SIZE),
        "limit",
        minimum=1,
        maximum=TIMELINE_MAX_PAGE_SIZE,
    )
    cursor = arguments.get("cursor") or None
    if cursor is not None and not isinstance(cursor, str):
        raise MCPToolError("cursor must be a string.")

    window = fetch_timeline_window(agent, cursor=cursor, direction=direction, limit=limit)
    return {
        "events": window.events,
        "oldest_cursor": window.oldest_cursor,
        "newest_cursor": window.newest_cursor,
        "has_more_older": window.has_more_older,
        "has_more_newer": window.has_more_newer,
        "processing_active": window.processing_active,
        "processing_snapshot": serialize_processing_snapshot(window.processing_snapshot),
    }


def _tool_list_agent_files(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    filespace = get_or_create_default_filespace(agent)
    nodes = (
        AgentFsNode.objects.alive()
        .filter(filespace=filespace)
        .only("id", "parent_id", "name", "path", "node_type", "size_bytes", "mime_type", "created_at", "updated_at")
        .order_by("parent_id", "node_type", "name")
    )
    return {
        "filespace": {"id": str(filespace.id), "name": filespace.name},
        "nodes": [_serialize_file_node(node) for node in nodes],
    }


def _tool_upload_agent_file(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    path = _required_string(arguments, "path", allow_blank=False)
    content_base64 = _required_string(arguments, "content_base64", allow_blank=False)
    mime_type = arguments.get("mime_type") or "application/octet-stream"
    if not isinstance(mime_type, str):
        raise MCPToolError("mime_type must be a string.")
    overwrite = _optional_bool(arguments.get("overwrite", False), "overwrite")

    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MCPToolError("content_base64 must be valid base64.") from exc

    result = write_bytes_to_dir(
        agent,
        content,
        path=path,
        mime_type=mime_type,
        overwrite=overwrite,
    )
    if result.get("status") != "ok":
        raise MCPToolError(result.get("message") or "File upload failed.", result)
    return result


def _agent_queryset(request):
    organization = _request_organization(request)
    if organization is None and not can_user_use_personal_agents_and_api(request.user):
        raise MCPToolError("Personal API access requires an active trial or plan.")

    queryset = (
        PersistentAgent.objects.non_eval()
        .alive()
        .select_related("browser_use_agent", "organization", "preferred_contact_endpoint", "preferred_llm_tier")
        .order_by("-created_at")
    )
    if organization is not None:
        return queryset.filter(organization=organization)
    return queryset.filter(user=request.user)


def _request_organization(request):
    auth = getattr(request, "auth", None)
    if isinstance(auth, ApiKey) and getattr(auth, "organization_id", None):
        return auth.organization
    return None


def _get_agent(request, raw_agent_id):
    agent_id = _parse_uuid(raw_agent_id, "agent_id")
    agent = _agent_queryset(request).filter(id=agent_id).first()
    if agent is None:
        raise MCPToolError("Agent not found or inaccessible.")
    return agent


def _resolve_peer_link(request, arguments):
    accessible = _agent_queryset(request).only("id")
    peer_link_id = arguments.get("peer_link_id")
    if peer_link_id:
        link_id = _parse_uuid(peer_link_id, "peer_link_id")
        link = (
            AgentPeerLink.objects.filter(id=link_id)
            .filter(Q(agent_a__in=accessible) | Q(agent_b__in=accessible))
            .select_related("agent_a", "agent_b", "created_by")
            .first()
        )
    else:
        agent = _get_agent(request, arguments.get("agent_id"))
        peer_agent = _get_agent(request, arguments.get("peer_agent_id"))
        pair_key = AgentPeerLink.build_pair_key(agent.id, peer_agent.id)
        link = (
            AgentPeerLink.objects.filter(pair_key=pair_key)
            .select_related("agent_a", "agent_b", "created_by")
            .first()
        )

    if link is None:
        raise MCPToolError("Peer link not found or inaccessible.")
    return link


def _serialize_agent(agent):
    return {
        "id": str(agent.id),
        "name": agent.name,
        "charter": agent.charter,
        "short_description": agent.short_description,
        "schedule": agent.schedule,
        "is_active": agent.is_active,
        "life_state": agent.life_state,
        "planning_state": agent.planning_state,
        "whitelist_policy": agent.whitelist_policy,
        "created_at": _iso(agent.created_at),
        "updated_at": _iso(agent.updated_at),
        "last_interaction_at": _iso(agent.last_interaction_at),
        "user_id": str(agent.user_id) if agent.user_id else None,
        "organization_id": str(agent.organization_id) if agent.organization_id else None,
        "browser_use_agent_id": str(agent.browser_use_agent_id) if agent.browser_use_agent_id else None,
        "preferred_contact_endpoint_id": (
            str(agent.preferred_contact_endpoint_id) if agent.preferred_contact_endpoint_id else None
        ),
        "preferred_llm_tier": getattr(getattr(agent, "preferred_llm_tier", None), "key", None),
        "proactive_opt_in": agent.proactive_opt_in,
        "proactive_last_trigger_at": _iso(agent.proactive_last_trigger_at),
    }


def _serialize_peer_link(link):
    return {
        "id": str(link.id),
        "agent_a": _serialize_agent_ref(link.agent_a),
        "agent_b": _serialize_agent_ref(link.agent_b),
        "is_enabled": link.is_enabled,
        "messages_per_window": link.messages_per_window,
        "window_hours": link.window_hours,
        "feature_flag": link.feature_flag or None,
        "pair_key": link.pair_key,
        "created_by_user_id": link.created_by_id,
        "created_at": _iso(link.created_at),
        "updated_at": _iso(link.updated_at),
    }


def _serialize_agent_ref(agent):
    return {
        "id": str(agent.id),
        "name": agent.name,
        "is_active": agent.is_active,
        "life_state": agent.life_state,
    }


def _serialize_message(message):
    return {
        "id": str(message.id),
        "owner_agent_id": str(message.owner_agent_id) if message.owner_agent_id else None,
        "conversation_id": str(message.conversation_id) if message.conversation_id else None,
        "is_outbound": message.is_outbound,
        "body": message.body,
        "timestamp": _iso(message.timestamp),
    }


def _serialize_file_node(node):
    return {
        "id": str(node.id),
        "parent_id": str(node.parent_id) if node.parent_id else None,
        "name": node.name,
        "path": node.path,
        "node_type": node.node_type,
        "size_bytes": node.size_bytes,
        "mime_type": node.mime_type or None,
        "created_at": _iso(node.created_at),
        "updated_at": _iso(node.updated_at),
    }


def _required_string(arguments, key, *, allow_blank):
    value = arguments.get(key)
    if not isinstance(value, str):
        raise MCPToolError(f"{key} must be a string.")
    if not allow_blank and not value.strip():
        raise MCPToolError(f"{key} cannot be blank.")
    return value.strip() if not allow_blank else value


def _optional_bool(value, key):
    if isinstance(value, bool):
        return value
    raise MCPToolError(f"{key} must be a boolean.")


def _bounded_int(value, key, *, minimum, maximum):
    if isinstance(value, bool):
        raise MCPToolError(f"{key} must be an integer.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MCPToolError(f"{key} must be an integer.") from exc
    if number < minimum or number > maximum:
        raise MCPToolError(f"{key} must be between {minimum} and {maximum}.")
    return number


def _parse_uuid(value, key):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise MCPToolError(f"{key} must be a valid UUID.") from exc


def _format_validation_error(exc):
    if hasattr(exc, "detail"):
        return _json_safe(exc.detail)
    if hasattr(exc, "message_dict"):
        return _json_safe(exc.message_dict)
    if hasattr(exc, "messages"):
        return _json_safe(exc.messages)
    return {"message": str(exc)}


def _json_safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, default=str))


def _iso(value):
    return value.isoformat() if value else None
