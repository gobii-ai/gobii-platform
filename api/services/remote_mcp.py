import base64
import binascii
import copy
import datetime
import json
import time
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Q

from rest_framework.exceptions import ValidationError as DRFValidationError

from api.agent.core.llm_config import (
    AgentLLMTier,
    get_allowed_tier_rank,
    get_llm_tier_description,
    get_llm_tier_label,
    get_system_default_tier,
    resolve_intelligence_tier_for_owner,
    resolve_preferred_tier_for_owner,
)
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
from api.services.agent_settings_resume import queue_settings_change_resume
from api.services.daily_credit_limits import calculate_daily_credit_slider_bounds, get_tier_credit_multiplier
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
from console.agent_chat.timeline import (
    DEFAULT_PAGE_SIZE as TIMELINE_DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE as TIMELINE_MAX_PAGE_SIZE,
    fetch_timeline_window,
    serialize_message_event,
    serialize_processing_snapshot,
)
from pages.account_info_cache import invalidate_account_info_cache
from util.trial_enforcement import can_user_use_personal_agents_and_api


MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {
    "name": "gobii",
    "title": "Gobii",
    "version": "2.19.0",
}
WAIT_DEFAULT_TIMEOUT_SECONDS = 10
WAIT_MAX_TIMEOUT_SECONDS = 30
WAIT_POLL_INTERVAL_SECONDS = 0.5
WAIT_EVENT_TYPES = {"message", "steps", "thinking", "plan"}
WAIT_FILTER_FIELDS = {
    "from_actor_type",
    "from_agent_id",
    "to_agent_id",
    "message_id",
    "peer_link_id",
    "channel",
    "status",
    "tool_name",
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


def _object_output(properties, *, required=()):
    schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _array_output(item_schema):
    return {"type": "array", "items": item_schema}


_STRING_OR_NULL = {"type": ["string", "null"]}
_INTEGER_OR_NULL = {"type": ["integer", "null"]}
_NUMBER_OR_STRING = {"type": ["number", "string"]}
_NUMBER_STRING_OR_NULL = {"type": ["integer", "number", "string", "null"]}
_ID_OR_NULL = {"type": ["integer", "string", "null"]}
_UUID_OUTPUT = {"type": "string", "format": "uuid"}

_AGENT_REF_OUTPUT = _object_output(
    {
        "id": _UUID_OUTPUT,
        "name": {"type": "string"},
        "is_active": {"type": "boolean"},
        "life_state": {"type": "string"},
    },
    required=("id", "name"),
)

_AGENT_OUTPUT = _object_output(
    {
        "id": _UUID_OUTPUT,
        "name": {"type": "string"},
        "charter": _STRING_OR_NULL,
        "short_description": _STRING_OR_NULL,
        "schedule": _STRING_OR_NULL,
        "is_active": {"type": "boolean"},
        "life_state": {"type": "string"},
        "planning_state": _STRING_OR_NULL,
        "whitelist_policy": {"type": "string"},
        "created_at": _STRING_OR_NULL,
        "updated_at": _STRING_OR_NULL,
        "last_interaction_at": _STRING_OR_NULL,
        "user_id": _STRING_OR_NULL,
        "organization_id": _STRING_OR_NULL,
        "browser_use_agent_id": _STRING_OR_NULL,
        "preferred_contact_endpoint_id": _STRING_OR_NULL,
        "preferred_llm_tier": _STRING_OR_NULL,
        "daily_credit_limit": _INTEGER_OR_NULL,
        "daily_credit_soft_target": _NUMBER_STRING_OR_NULL,
        "daily_credit_hard_limit": _NUMBER_STRING_OR_NULL,
        "proactive_opt_in": {"type": "boolean"},
        "proactive_last_trigger_at": _STRING_OR_NULL,
    },
    required=("id", "name", "schedule", "is_active"),
)

_LINK_OUTPUT = _object_output(
    {
        "id": _UUID_OUTPUT,
        "agent_a": _AGENT_REF_OUTPUT,
        "agent_b": _AGENT_REF_OUTPUT,
        "is_enabled": {"type": "boolean"},
        "messages_per_window": {"type": "integer"},
        "window_hours": {"type": "integer"},
        "feature_flag": _STRING_OR_NULL,
        "pair_key": {"type": "string"},
        "created_by_user_id": _ID_OR_NULL,
        "created_at": _STRING_OR_NULL,
        "updated_at": _STRING_OR_NULL,
    },
    required=("id", "agent_a", "agent_b", "is_enabled"),
)

_TIMELINE_EVENT_OUTPUT = _object_output(
    {
        "kind": {"type": "string"},
        "cursor": _STRING_OR_NULL,
    },
    required=("kind",),
)

_MESSAGE_OUTPUT = _object_output(
    {
        "id": _UUID_OUTPUT,
        "owner_agent_id": _STRING_OR_NULL,
        "conversation_id": _STRING_OR_NULL,
        "is_outbound": {"type": "boolean"},
        "body": {"type": "string"},
        "timestamp": _STRING_OR_NULL,
    },
    required=("id", "body"),
)

_FILE_NODE_OUTPUT = _object_output(
    {
        "id": _UUID_OUTPUT,
        "parent_id": _STRING_OR_NULL,
        "name": {"type": "string"},
        "path": {"type": "string"},
        "node_type": {"type": "string"},
        "size_bytes": _INTEGER_OR_NULL,
        "mime_type": _STRING_OR_NULL,
        "created_at": _STRING_OR_NULL,
        "updated_at": _STRING_OR_NULL,
    },
    required=("id", "name", "path", "node_type"),
)


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
        "outputSchema": _object_output(
            {
                "agents": _array_output(_AGENT_OUTPUT),
                "page": {"type": "integer"},
                "page_size": {"type": "integer"},
                "total": {"type": "integer"},
                "has_next": {"type": "boolean"},
            },
            required=("agents", "page", "page_size", "total", "has_next"),
        ),
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
        "outputSchema": _object_output({"agent": _AGENT_OUTPUT}, required=("agent",)),
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
                "preferred_llm_tier": {
                    "type": "string",
                    "description": "Preferred intelligence tier key, such as standard, premium, max, ultra, or ultra_max.",
                },
                "daily_credit_limit": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Soft daily credit target. Null means unlimited. Gobii enforces a hard stop at the configured multiplier.",
                },
                "whitelist_policy": {
                    "type": "string",
                    "enum": ["default", "manual"],
                    "description": "Contact allowlist policy.",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": _object_output({"agent": _AGENT_OUTPUT}, required=("agent",)),
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
                "preferred_llm_tier": {
                    "type": "string",
                    "description": "Preferred intelligence tier key, such as standard, premium, max, ultra, or ultra_max.",
                },
                "daily_credit_limit": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Soft daily credit target. Null means unlimited.",
                },
                "whitelist_policy": {"type": "string", "enum": ["default", "manual"]},
                "proactive_opt_in": {"type": "boolean"},
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "outputSchema": _object_output({"agent": _AGENT_OUTPUT}, required=("agent",)),
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
        "outputSchema": _object_output(
            {
                "status": {"type": "string"},
                "changed": {"type": "boolean"},
                "agent": _AGENT_OUTPUT,
            },
            required=("status", "changed", "agent"),
        ),
        "annotations": {"destructiveHint": True},
    },
    {
        "name": "gobii_get_agent_config_options",
        "title": "Get Agent Config Options",
        "description": "Discover supported Gobii agent configuration fields, intelligence tiers, daily credit limits, schedules, and policies for this API key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Optional persistent agent UUID to include current per-agent config."),
            },
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {
                "owner": _object_output(
                    {
                        "type": {"type": "string"},
                        "id": {"type": ["integer", "string"]},
                        "name": _STRING_OR_NULL,
                    },
                    required=("type", "id"),
                ),
                "agent": {"anyOf": [_AGENT_OUTPUT, {"type": "null"}]},
                "fields": _object_output(
                    {
                        "preferred_llm_tier": _object_output(
                            {
                                "type": {"type": "string"},
                                "current_system_default": {"type": "string"},
                                "max_allowed_tier": {"type": "string"},
                                "max_allowed_rank": {"type": "integer"},
                                "options": _array_output(
                                    _object_output(
                                        {
                                            "key": {"type": "string"},
                                            "label": {"type": "string"},
                                            "description": {"type": "string"},
                                            "rank": {"type": "integer"},
                                            "credit_multiplier": _NUMBER_OR_STRING,
                                            "is_default": {"type": "boolean"},
                                            "allowed": {"type": "boolean"},
                                        },
                                        required=("key", "label", "allowed"),
                                    )
                                ),
                            },
                            required=("type", "options"),
                        ),
                        "daily_credit_limit": _object_output(
                            {
                                "type": {"type": "string"},
                                "null_behavior": {"type": "string"},
                                "hard_limit_multiplier": _NUMBER_OR_STRING,
                                "default_daily_credit_target": _NUMBER_OR_STRING,
                                "recommended_min": _NUMBER_OR_STRING,
                                "recommended_max": _NUMBER_OR_STRING,
                                "step": _NUMBER_OR_STRING,
                                "tier_credit_multiplier": _NUMBER_OR_STRING,
                                "enforced_by_agent_runtime": {"type": "boolean"},
                            },
                            required=("type", "null_behavior"),
                        ),
                    },
                    required=("preferred_llm_tier", "daily_credit_limit"),
                ),
                "unsupported_remote_mcp_v1_fields": _array_output({"type": "string"}),
            },
            required=("owner", "fields", "unsupported_remote_mcp_v1_fields"),
        ),
        "annotations": {"readOnlyHint": True},
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
        "outputSchema": _object_output({"links": _array_output(_LINK_OUTPUT)}, required=("links",)),
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
        "outputSchema": _object_output(
            {"link": _LINK_OUTPUT, "created": {"type": "boolean"}},
            required=("link", "created"),
        ),
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
        "outputSchema": _object_output(
            {"status": {"type": "string"}, "link": _LINK_OUTPUT},
            required=("status", "link"),
        ),
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
        "outputSchema": _object_output(
            {
                "status": {"type": "string"},
                "accepted_state": {"type": "string"},
                "message_id": _UUID_OUTPUT,
                "agent_id": _UUID_OUTPUT,
                "cursor": _STRING_OR_NULL,
                "latest_cursor": _STRING_OR_NULL,
                "created_at": _STRING_OR_NULL,
                "actor": _object_output(
                    {
                        "type": {"type": "string"},
                        "source": {"type": "string"},
                        "user_id": {"type": ["integer", "string"]},
                    },
                    required=("type", "source"),
                ),
                "message": _MESSAGE_OUTPUT,
                "timeline_event": _TIMELINE_EVENT_OUTPUT,
                "conversation_id": _UUID_OUTPUT,
                "attachment_count": {"type": "integer"},
            },
            required=("status", "message_id", "agent_id", "cursor", "latest_cursor"),
        ),
    },
    {
        "name": "gobii_get_agent_timeline",
        "title": "Get Agent Timeline",
        "description": "Fetch recent chat, task, thinking, and processing events for a persistent agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Persistent agent UUID."),
                "after_cursor": {
                    "type": ["string", "null"],
                    "description": "Return events newer than this durable timeline cursor. Preferred for V1 clients.",
                },
                "cursor": {"type": ["string", "null"], "description": "Cursor from a previous timeline result."},
                "direction": {"type": "string", "enum": ["initial", "older", "newer"], "default": "initial"},
                "limit": {"type": "integer", "minimum": 1, "maximum": TIMELINE_MAX_PAGE_SIZE, "default": TIMELINE_DEFAULT_PAGE_SIZE},
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {
                "events": _array_output(_TIMELINE_EVENT_OUTPUT),
                "next_cursor": _STRING_OR_NULL,
                "latest_cursor": _STRING_OR_NULL,
                "oldest_cursor": _STRING_OR_NULL,
                "newest_cursor": _STRING_OR_NULL,
                "has_more": {"type": "boolean"},
                "has_more_older": {"type": "boolean"},
                "has_more_newer": {"type": "boolean"},
                "processing_active": {"type": "boolean"},
                "processing_snapshot": _object_output({}),
            },
            required=("events", "latest_cursor", "has_more"),
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "gobii_wait_for_agent_event",
        "title": "Wait For Agent Timeline Event",
        "description": "Bounded long-poll over an agent's unified timeline using durable cursors and supported structured filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Persistent agent UUID."),
                "after_cursor": {
                    "type": ["string", "null"],
                    "description": "Only consider timeline events newer than this cursor.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": WAIT_MAX_TIMEOUT_SECONDS,
                    "default": WAIT_DEFAULT_TIMEOUT_SECONDS,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": TIMELINE_MAX_PAGE_SIZE, "default": 20},
                "event_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(WAIT_EVENT_TYPES)},
                    "description": "Optional event kinds to match.",
                },
                "filters": {
                    "type": "object",
                    "properties": {
                        "from_actor_type": {"type": "string", "enum": ["agent", "human_user", "external", "system"]},
                        "from_agent_id": _agent_schema("Peer/source agent UUID for peer-agent message events."),
                        "to_agent_id": _agent_schema("Target agent UUID for message events."),
                        "message_id": {"type": "string", "format": "uuid"},
                        "peer_link_id": {"type": "string", "format": "uuid"},
                        "channel": {"type": "string"},
                        "status": {"type": "string", "description": "Tool-call status for steps events."},
                        "tool_name": {"type": "string", "description": "Tool name for steps events."},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {
                "matched": {"type": "boolean"},
                "timed_out": {"type": "boolean"},
                "events": _array_output(_TIMELINE_EVENT_OUTPUT),
                "next_cursor": _STRING_OR_NULL,
                "latest_cursor": _STRING_OR_NULL,
                "waited_seconds": _NUMBER_OR_STRING,
            },
            required=("matched", "timed_out", "events", "waited_seconds"),
        ),
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
        "outputSchema": _object_output(
            {
                "filespace": _object_output(
                    {"id": _UUID_OUTPUT, "name": {"type": "string"}},
                    required=("id", "name"),
                ),
                "nodes": _array_output(_FILE_NODE_OUTPUT),
            },
            required=("filespace", "nodes"),
        ),
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
        "outputSchema": _object_output(
            {
                "status": {"type": "string"},
                "path": {"type": "string"},
                "node_id": _UUID_OUTPUT,
                "filename": {"type": "string"},
                "message": _STRING_OR_NULL,
            },
            required=("status",),
        ),
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOL_DEFINITIONS}
TOOL_BY_NAME = {tool["name"]: tool for tool in TOOL_DEFINITIONS}


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
    _reject_unknown_arguments(name, arguments)

    handler = {
        "gobii_list_agents": _tool_list_agents,
        "gobii_get_agent": _tool_get_agent,
        "gobii_create_agent": _tool_create_agent,
        "gobii_update_agent": _tool_update_agent,
        "gobii_archive_agent": _tool_archive_agent,
        "gobii_get_agent_config_options": _tool_get_agent_config_options,
        "gobii_list_agent_links": _tool_list_agent_links,
        "gobii_link_agents": _tool_link_agents,
        "gobii_unlink_agents": _tool_unlink_agents,
        "gobii_send_agent_message": _tool_send_agent_message,
        "gobii_get_agent_timeline": _tool_get_agent_timeline,
        "gobii_wait_for_agent_event": _tool_wait_for_agent_event,
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
    allowed_fields = {
        "name",
        "charter",
        "schedule",
        "is_active",
        "preferred_llm_tier",
        "daily_credit_limit",
        "whitelist_policy",
    }
    payload = {key: arguments[key] for key in allowed_fields if key in arguments}
    _normalize_agent_config_payload(request, payload)
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
    allowed_fields = {
        "name",
        "charter",
        "schedule",
        "is_active",
        "preferred_llm_tier",
        "daily_credit_limit",
        "whitelist_policy",
        "proactive_opt_in",
    }
    payload = {key: arguments[key] for key in allowed_fields if key in arguments}
    if not payload:
        raise MCPToolError("At least one mutable field is required.")
    _normalize_agent_config_payload(request, payload, agent=agent)

    serializer = PersistentAgentSerializer(
        agent,
        data=payload,
        partial=True,
        context={"request": request, "organization": _request_organization(request)},
    )
    try:
        previous_daily_credit_limit = agent.daily_credit_limit
        previous_tier_id = agent.preferred_llm_tier_id
        previous_tier_key = getattr(getattr(agent, "preferred_llm_tier", None), "key", "standard")
        serializer.is_valid(raise_exception=True)
        agent = serializer.save()
    except (DRFValidationError, DjangoValidationError) as exc:
        raise MCPToolError("Agent update failed.", _format_validation_error(exc)) from exc
    _queue_agent_settings_resume_if_needed(
        agent,
        previous_daily_credit_limit=previous_daily_credit_limit,
        previous_tier_id=previous_tier_id,
        previous_tier_key=previous_tier_key,
    )

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


def _tool_get_agent_config_options(request, arguments):
    agent = None
    if arguments.get("agent_id"):
        agent = _get_agent(request, arguments.get("agent_id"))
    owner = _agent_owner_for_request(request, agent=agent)
    daily_credit_options = _build_daily_credit_options(owner, getattr(agent, "preferred_llm_tier", None))
    return {
        "owner": _serialize_owner_ref(owner),
        "agent": _serialize_agent(agent) if agent else None,
        "fields": {
            "preferred_llm_tier": _build_intelligence_options(owner),
            "daily_credit_limit": daily_credit_options,
            "schedule": {
                "type": "string_or_null",
                "required": False,
                "null_behavior": "unscheduled",
                "accepted_formats": [
                    "cron-like schedule expressions accepted by Gobii's ScheduleParser",
                    "@daily",
                    "@every interval",
                ],
            },
            "whitelist_policy": {
                "type": "string",
                "options": [
                    {"value": value, "label": label}
                    for value, label in PersistentAgent.WhitelistPolicy.choices
                ],
            },
            "is_active": {"type": "boolean"},
            "proactive_opt_in": {"type": "boolean", "mutable_on_update": True},
        },
        "unsupported_remote_mcp_v1_fields": [
            "arbitrary_url_file_fetch",
            "ad_hoc_runtime_session",
            "separate_task_or_run_abstraction",
        ],
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

    event = serialize_message_event(message)
    cursor = event.get("cursor")
    return {
        "status": "queued" if trigger_processing else "stored",
        "accepted_state": "queued" if trigger_processing else "stored",
        "message_id": str(message.id),
        "agent_id": str(agent.id),
        "cursor": cursor,
        "latest_cursor": cursor,
        "created_at": _iso(message.timestamp),
        "actor": {
            "type": "human_user",
            "source": "remote_mcp",
            "user_id": request.user.id,
        },
        "message": _serialize_message(message),
        "timeline_event": event,
        "conversation_id": str(conversation.id),
        "attachment_count": len(resolved_attachments),
    }


def _tool_get_agent_timeline(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    after_cursor = arguments.get("after_cursor")
    direction = "newer" if after_cursor else arguments.get("direction") or "initial"
    if direction not in {"initial", "older", "newer"}:
        raise MCPToolError("direction must be one of initial, older, or newer.")
    limit = _bounded_int(
        arguments.get("limit", TIMELINE_DEFAULT_PAGE_SIZE),
        "limit",
        minimum=1,
        maximum=TIMELINE_MAX_PAGE_SIZE,
    )
    cursor = after_cursor or arguments.get("cursor") or None
    if cursor is not None and not isinstance(cursor, str):
        raise MCPToolError("cursor/after_cursor must be a string.")
    _validate_timeline_cursor(cursor, "cursor/after_cursor")

    window = fetch_timeline_window(agent, cursor=cursor, direction=direction, limit=limit)
    return {
        "events": window.events,
        "next_cursor": window.newest_cursor,
        "latest_cursor": window.newest_cursor,
        "oldest_cursor": window.oldest_cursor,
        "newest_cursor": window.newest_cursor,
        "has_more": window.has_more_newer if direction == "newer" else window.has_more_older,
        "has_more_older": window.has_more_older,
        "has_more_newer": window.has_more_newer,
        "processing_active": window.processing_active,
        "processing_snapshot": serialize_processing_snapshot(window.processing_snapshot),
    }


def _tool_wait_for_agent_event(request, arguments):
    agent = _get_agent(request, arguments.get("agent_id"))
    after_cursor = arguments.get("after_cursor") or None
    if after_cursor is not None and not isinstance(after_cursor, str):
        raise MCPToolError("after_cursor must be a string.")
    _validate_timeline_cursor(after_cursor, "after_cursor")

    timeout_seconds = _bounded_int(
        arguments.get("timeout_seconds", WAIT_DEFAULT_TIMEOUT_SECONDS),
        "timeout_seconds",
        minimum=0,
        maximum=WAIT_MAX_TIMEOUT_SECONDS,
    )
    limit = _bounded_int(arguments.get("limit", 20), "limit", minimum=1, maximum=TIMELINE_MAX_PAGE_SIZE)
    event_types = _normalize_wait_event_types(arguments.get("event_types"))
    filters = _normalize_wait_filters(arguments.get("filters"))

    start = time.monotonic()
    deadline = start + timeout_seconds
    latest_cursor = after_cursor
    events: list[dict] = []

    while True:
        direction = "newer" if latest_cursor else "initial"
        window = fetch_timeline_window(agent, cursor=latest_cursor, direction=direction, limit=limit)
        if window.newest_cursor:
            latest_cursor = window.newest_cursor
        events = [
            event
            for event in window.events
            if _wait_event_matches(agent, event, event_types=event_types, filters=filters)
        ]
        if events:
            waited_seconds = round(time.monotonic() - start, 3)
            return {
                "matched": True,
                "timed_out": False,
                "events": events,
                "next_cursor": latest_cursor,
                "latest_cursor": latest_cursor,
                "waited_seconds": waited_seconds,
            }
        if time.monotonic() >= deadline:
            waited_seconds = round(time.monotonic() - start, 3)
            return {
                "matched": False,
                "timed_out": True,
                "events": [],
                "next_cursor": latest_cursor,
                "latest_cursor": latest_cursor,
                "waited_seconds": waited_seconds,
            }
        sleep_seconds = min(WAIT_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic()))
        if sleep_seconds:
            time.sleep(sleep_seconds)


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


def _reject_unknown_arguments(name, arguments):
    tool = TOOL_BY_NAME[name]
    properties = tool.get("inputSchema", {}).get("properties", {})
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise MCPToolError(
            "Unsupported tool argument(s).",
            {
                "unsupported_fields": unknown,
                "supported_fields": sorted(properties),
            },
        )


def _normalize_agent_config_payload(request, payload, *, agent=None):
    if "schedule" in payload and payload["schedule"] == "":
        payload["schedule"] = None
    if "preferred_llm_tier" in payload:
        tier_value = payload.get("preferred_llm_tier")
        if not isinstance(tier_value, str) or not tier_value.strip():
            raise MCPToolError(
                "preferred_llm_tier must be a supported intelligence tier key.",
                {"field": "preferred_llm_tier"},
            )
        owner = _agent_owner_for_request(request, agent=agent)
        requested_key = tier_value.strip().lower()
        if requested_key not in {tier.value for tier in AgentLLMTier}:
            raise MCPToolError(
                "preferred_llm_tier is not a known intelligence tier key.",
                {"field": "preferred_llm_tier", "requested": requested_key},
            )
        try:
            resolved = resolve_preferred_tier_for_owner(owner, requested_key)
            tier = resolve_intelligence_tier_for_owner(owner, requested_key)
        except ValueError as exc:
            raise MCPToolError(
                "preferred_llm_tier is not supported for this API key.",
                {"field": "preferred_llm_tier", "requested": requested_key},
            ) from exc
        if resolved.value != requested_key:
            raise MCPToolError(
                "preferred_llm_tier exceeds the owner plan or quota limit.",
                {
                    "field": "preferred_llm_tier",
                    "requested": requested_key,
                    "max_allowed": resolved.value,
                },
            )
        payload["preferred_llm_tier"] = tier.key
    if "daily_credit_limit" in payload:
        payload["daily_credit_limit"] = _normalize_daily_credit_limit(payload.get("daily_credit_limit"))


def _normalize_daily_credit_limit(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise MCPToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
    if isinstance(value, int):
        limit = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise MCPToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
        limit = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped.isdecimal():
            raise MCPToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
        limit = int(stripped)
    else:
        raise MCPToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
    if limit < 1:
        raise MCPToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
    return limit


def _queue_agent_settings_resume_if_needed(
    agent,
    *,
    previous_daily_credit_limit,
    previous_tier_id,
    previous_tier_key,
):
    daily_limit_changed = agent.daily_credit_limit != previous_daily_credit_limit
    preferred_tier_changed = agent.preferred_llm_tier_id != previous_tier_id
    if not daily_limit_changed and not preferred_tier_changed:
        return
    queue_settings_change_resume(
        agent,
        daily_credit_limit_changed=daily_limit_changed,
        previous_daily_credit_limit=previous_daily_credit_limit,
        preferred_llm_tier_changed=preferred_tier_changed,
        previous_preferred_llm_tier_key=previous_tier_key,
        source="remote_mcp_update_agent",
    )


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


def _agent_owner_for_request(request, *, agent=None):
    if agent is not None:
        return agent.organization or agent.user
    return _request_organization(request) or request.user


def _serialize_owner_ref(owner):
    if owner is None:
        return None
    owner_meta = getattr(owner, "_meta", None)
    model_name = getattr(owner_meta, "model_name", "")
    if model_name == "organization":
        return {"type": "organization", "id": str(owner.id), "name": getattr(owner, "name", "")}
    return {"type": "user", "id": owner.id}


def _build_intelligence_options(owner):
    max_allowed = resolve_preferred_tier_for_owner(owner, AgentLLMTier.ULTRA_MAX.value)
    max_allowed_rank = get_allowed_tier_rank(max_allowed)
    tiers = list(
        PersistentAgent._meta.get_field("preferred_llm_tier").remote_field.model.objects.order_by("rank", "key")
    )
    current_default = get_system_default_tier().value
    options = []
    for tier in tiers:
        tier_key = tier.key
        try:
            tier_enum = AgentLLMTier(tier_key)
            allowed = get_allowed_tier_rank(tier_enum) <= max_allowed_rank
        except ValueError:
            allowed = False
        options.append(
            {
                "key": tier_key,
                "label": get_llm_tier_label(tier_key, tier.display_name),
                "description": get_llm_tier_description(tier_key),
                "rank": tier.rank,
                "credit_multiplier": tier.credit_multiplier,
                "is_default": tier.is_default or tier_key == current_default,
                "allowed": allowed,
            }
        )
    return {
        "type": "string",
        "current_system_default": current_default,
        "max_allowed_tier": max_allowed.value,
        "max_allowed_rank": max_allowed_rank,
        "options": options,
    }


def _build_daily_credit_options(owner, tier):
    credit_settings = get_daily_credit_settings_for_owner(owner)
    multiplier = get_tier_credit_multiplier(tier)
    slider_bounds = calculate_daily_credit_slider_bounds(credit_settings, tier_multiplier=multiplier)
    return {
        "type": "integer_or_null",
        "null_behavior": "unlimited",
        "soft_target_description": "Preferred daily credit target before agents are asked to slow down.",
        "hard_limit_description": "Gobii enforces a hard stop at soft target multiplied by hard_limit_multiplier.",
        "hard_limit_multiplier": credit_settings.hard_limit_multiplier,
        "default_daily_credit_target": credit_settings.default_daily_credit_target,
        "recommended_min": slider_bounds["slider_min"],
        "recommended_max": slider_bounds["slider_limit_max"],
        "step": slider_bounds["slider_step"],
        "tier_credit_multiplier": multiplier,
        "enforced_by_agent_runtime": True,
    }


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
        "daily_credit_limit": agent.daily_credit_limit,
        "daily_credit_soft_target": agent.get_daily_credit_soft_target(),
        "daily_credit_hard_limit": agent.get_daily_credit_hard_limit(),
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


def _validate_timeline_cursor(cursor, key):
    if not cursor:
        return
    parts = cursor.split(":", 2)
    if len(parts) != 3:
        raise MCPToolError(f"{key} must be a valid Gobii timeline cursor.")
    value, kind, identifier = parts
    try:
        int(value)
    except ValueError as exc:
        raise MCPToolError(f"{key} must be a valid Gobii timeline cursor.") from exc
    if kind not in {"message", "step", "thinking", "kanban", "plan"} or not identifier:
        raise MCPToolError(f"{key} must be a valid Gobii timeline cursor.")


def _normalize_wait_event_types(value):
    if value in (None, []):
        return None
    if not isinstance(value, list):
        raise MCPToolError("event_types must be an array.", {"field": "event_types"})
    normalized = set()
    invalid = []
    for item in value:
        if not isinstance(item, str):
            invalid.append(item)
            continue
        event_type = item.strip()
        if event_type not in WAIT_EVENT_TYPES:
            invalid.append(item)
        else:
            normalized.add(event_type)
    if invalid:
        raise MCPToolError(
            "event_types contains unsupported values.",
            {"field": "event_types", "unsupported_values": invalid, "supported_values": sorted(WAIT_EVENT_TYPES)},
        )
    return normalized


def _normalize_wait_filters(value):
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise MCPToolError("filters must be an object.", {"field": "filters"})
    unsupported = sorted(set(value) - WAIT_FILTER_FIELDS)
    if unsupported:
        raise MCPToolError(
            "filters contains unsupported fields.",
            {"unsupported_fields": unsupported, "supported_fields": sorted(WAIT_FILTER_FIELDS)},
        )
    filters = {}
    for key, raw in value.items():
        if raw in (None, ""):
            continue
        if key in {"from_agent_id", "to_agent_id", "message_id", "peer_link_id"}:
            filters[key] = str(_parse_uuid(raw, key))
        elif key in {"from_actor_type", "channel", "status", "tool_name"}:
            if not isinstance(raw, str) or not raw.strip():
                raise MCPToolError(f"filters.{key} must be a non-empty string.")
            filters[key] = raw.strip()
    if "from_actor_type" in filters and filters["from_actor_type"] not in {"agent", "human_user", "external", "system"}:
        raise MCPToolError(
            "filters.from_actor_type contains an unsupported value.",
            {"field": "from_actor_type", "supported_values": ["agent", "human_user", "external", "system"]},
        )
    return filters


def _wait_event_matches(agent, event, *, event_types, filters):
    kind = event.get("kind")
    if event_types is not None and kind not in event_types:
        return False
    if not filters:
        return True
    for key, expected in filters.items():
        if event.get("kind") == "steps" and key in {"status", "tool_name"}:
            if not _steps_event_has_value(event, key, expected):
                return False
            continue
        if _wait_event_field_value(agent, event, key) != expected:
            return False
    return True


def _steps_event_has_value(event, key, expected):
    entry_key = "status" if key == "status" else "toolName"
    return any((entry.get(entry_key) or "") == expected for entry in event.get("entries") or [])


def _wait_event_field_value(agent, event, key):
    kind = event.get("kind")
    if kind == "message":
        message = event.get("message") or {}
        peer_agent = message.get("peerAgent") or {}
        is_peer = bool(message.get("isPeer"))
        is_outbound = bool(message.get("isOutbound"))
        if key == "from_actor_type":
            if is_peer:
                return "agent"
            if is_outbound:
                return "agent"
            if message.get("senderUserId"):
                return "human_user"
            return "external"
        if key == "from_agent_id":
            if is_peer and not is_outbound:
                return peer_agent.get("id")
            if is_outbound:
                return str(agent.id)
            return None
        if key == "to_agent_id":
            if is_peer and is_outbound:
                return peer_agent.get("id")
            if not is_outbound:
                return str(agent.id)
            return None
        if key == "message_id":
            return message.get("id")
        if key == "peer_link_id":
            return message.get("peerLinkId")
        if key == "channel":
            return message.get("channel")
        return None
    if kind == "steps":
        if key == "from_actor_type":
            return "system"
        return None
    if kind in {"thinking", "plan"} and key == "from_actor_type":
        return "system"
    return None


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
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=str)]
    return str(value)


def _iso(value):
    return value.isoformat() if value else None
