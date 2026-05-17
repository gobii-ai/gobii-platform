import base64
import binascii
import copy
import datetime
import json
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
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
    Organization,
    OrganizationMembership,
    PersistentAgent,
    build_web_user_address,
)
from api.serializers import PersistentAgentSerializer
from api.services.agent_settings_resume import queue_settings_change_resume
from api.services.agent_debug_trace import (
    DEBUG_TRACE_DEFAULT_INCLUDE,
    DEBUG_TRACE_DEFAULT_LIMIT,
    DEBUG_TRACE_DEFAULT_RECENT_MINUTES,
    DEBUG_TRACE_DETAIL_LEVELS,
    DEBUG_TRACE_INCLUDE_SECTIONS,
    DEBUG_TRACE_MAX_LIMIT,
    DEBUG_TRACE_MAX_RECENT_MINUTES,
    DEBUG_TRACE_TOOL_NAME,
    AgentDebugTraceValidationError,
    build_agent_debug_trace,
)
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
_SCOPE_PARAM_PROPERTIES = {
    "user_id": {
        "type": ["integer", "string", "null"],
        "description": (
            "Optional target Django user id. Staff/superuser API keys only; "
            "ordinary API keys receive a structured tool error when this is supplied."
        ),
    },
    "organization_id": {
        "type": ["string", "null"],
        "format": "uuid",
        "description": (
            "Optional target organization UUID. Staff/superuser API keys only; "
            "ordinary API keys receive a structured tool error when this is supplied."
        ),
    },
}

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

_ACCESS_METADATA_OUTPUT = _object_output(
    {
        "admin_access": {"type": "boolean"},
        "access_scope": {"type": "string"},
        "operator_user_id": _ID_OR_NULL,
        "target_user_id": _ID_OR_NULL,
        "target_organization_id": _STRING_OR_NULL,
        "requested_user_id": _ID_OR_NULL,
        "requested_organization_id": _STRING_OR_NULL,
    },
    required=("admin_access", "access_scope"),
)

_DEBUG_TRACE_EVENT_OUTPUT = _object_output(
    {
        "kind": {"type": "string"},
        "id": _STRING_OR_NULL,
        "timestamp": _STRING_OR_NULL,
    },
    required=("kind",),
)

_DEBUG_TRACE_OUTPUT = _object_output(
    {
        "agent": _AGENT_REF_OUTPUT,
        "scope": _object_output(
            {
                "agent_id": _UUID_OUTPUT,
                "requested_at": _STRING_OR_NULL,
                "since": _STRING_OR_NULL,
                "until": _STRING_OR_NULL,
                "recent_minutes": _INTEGER_OR_NULL,
                "cursor": _STRING_OR_NULL,
                "limit": {"type": "integer"},
                "include": _array_output({"type": "string"}),
                "detail": {"type": "string"},
                "eval_run_id": _STRING_OR_NULL,
            },
            required=("agent_id", "limit", "include", "detail"),
        ),
        "timeline": _object_output(
            {
                "events": _array_output(_TIMELINE_EVENT_OUTPUT),
                "latest_cursor": _STRING_OR_NULL,
                "oldest_cursor": _STRING_OR_NULL,
                "newest_cursor": _STRING_OR_NULL,
                "has_more_older": {"type": "boolean"},
                "has_more_newer": {"type": "boolean"},
                "processing_active": {"type": "boolean"},
                "processing_snapshot": _object_output({}),
            }
        ),
        "audit_events": _array_output(_DEBUG_TRACE_EVENT_OUTPUT),
        "audit": _object_output(
            {
                "source": {"type": "string"},
                "has_more": {"type": "boolean"},
                "next_cursor": _STRING_OR_NULL,
                "returned": {"type": "integer"},
            }
        ),
        "completions": _object_output({}),
        "eval_debug_artifacts": _object_output({}),
        "diagnostics": _object_output({}),
        "redaction": _object_output(
            {
                "mode": {"type": "string"},
                "replacement": {"type": "string"},
                "notes": _array_output({"type": "string"}),
            },
            required=("mode", "replacement"),
        ),
        "warnings": _array_output({"type": "string"}),
        "access": _ACCESS_METADATA_OUTPUT,
    },
    required=("agent", "scope", "redaction", "warnings"),
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
                **_SCOPE_PARAM_PROPERTIES,
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
                "access": _ACCESS_METADATA_OUTPUT,
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
            "properties": {"agent_id": _agent_schema("Persistent agent UUID."), **_SCOPE_PARAM_PROPERTIES},
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {"agent": _AGENT_OUTPUT, "access": _ACCESS_METADATA_OUTPUT},
            required=("agent",),
        ),
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
                **_SCOPE_PARAM_PROPERTIES,
            },
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {"agent": _AGENT_OUTPUT, "access": _ACCESS_METADATA_OUTPUT},
            required=("agent",),
        ),
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
                **_SCOPE_PARAM_PROPERTIES,
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {"agent": _AGENT_OUTPUT, "access": _ACCESS_METADATA_OUTPUT},
            required=("agent",),
        ),
    },
    {
        "name": "gobii_archive_agent",
        "title": "Archive Gobii Agent",
        "description": "Soft-delete a persistent Gobii agent using Gobii's normal archive/delete behavior.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": _agent_schema("Persistent agent UUID."), **_SCOPE_PARAM_PROPERTIES},
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {
                "status": {"type": "string"},
                "changed": {"type": "boolean"},
                "agent": _AGENT_OUTPUT,
                "access": _ACCESS_METADATA_OUTPUT,
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
                **_SCOPE_PARAM_PROPERTIES,
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
                "access": _ACCESS_METADATA_OUTPUT,
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
                **_SCOPE_PARAM_PROPERTIES,
            },
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {"links": _array_output(_LINK_OUTPUT), "access": _ACCESS_METADATA_OUTPUT},
            required=("links",),
        ),
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
                **_SCOPE_PARAM_PROPERTIES,
            },
            "required": ["agent_id", "peer_agent_id"],
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {"link": _LINK_OUTPUT, "created": {"type": "boolean"}, "access": _ACCESS_METADATA_OUTPUT},
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
                **_SCOPE_PARAM_PROPERTIES,
            },
            "additionalProperties": False,
        },
        "outputSchema": _object_output(
            {"status": {"type": "string"}, "link": _LINK_OUTPUT, "access": _ACCESS_METADATA_OUTPUT},
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
                **_SCOPE_PARAM_PROPERTIES,
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
                "access": _ACCESS_METADATA_OUTPUT,
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
                    "description": (
                        "Return events strictly newer than this durable timeline cursor. "
                        "The event with this exact cursor is excluded."
                    ),
                },
                "cursor": {"type": ["string", "null"], "description": "Cursor from a previous timeline result."},
                "direction": {"type": "string", "enum": ["initial", "older", "newer"], "default": "initial"},
                "limit": {"type": "integer", "minimum": 1, "maximum": TIMELINE_MAX_PAGE_SIZE, "default": TIMELINE_DEFAULT_PAGE_SIZE},
                **_SCOPE_PARAM_PROPERTIES,
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
                "access": _ACCESS_METADATA_OUTPUT,
            },
            required=("events", "latest_cursor", "has_more"),
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": DEBUG_TRACE_TOOL_NAME,
        "title": "Get Agent Debug Trace",
        "description": (
            "Fetch bounded, sanitized debugging information for one accessible Gobii agent, including "
            "recent timeline/audit events, tool calls, completions, prompt archive metadata, usage/cost, "
            "eval artifacts, and diagnostics. Raw prompt archives and secret-like values are not returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": _agent_schema("Persistent agent UUID."),
                "cursor": {
                    "type": ["string", "null"],
                    "description": "Optional audit/debug cursor returned as audit.next_cursor for older debug events.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": DEBUG_TRACE_MAX_LIMIT,
                    "default": DEBUG_TRACE_DEFAULT_LIMIT,
                    "description": "Maximum items per bounded debug section.",
                },
                "recent_minutes": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": DEBUG_TRACE_MAX_RECENT_MINUTES,
                    "default": DEBUG_TRACE_DEFAULT_RECENT_MINUTES,
                    "description": (
                        "Recent time window when no cursor or explicit since is supplied. Null disables "
                        "the time window and relies on limit/cursor bounds."
                    ),
                },
                "since": {
                    "type": ["string", "null"],
                    "description": "Optional ISO8601 lower time bound. Cannot be combined with recent_minutes.",
                },
                "until": {
                    "type": ["string", "null"],
                    "description": "Optional ISO8601 upper time bound. Defaults to request time.",
                },
                "include": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(DEBUG_TRACE_INCLUDE_SECTIONS)},
                    "default": list(DEBUG_TRACE_DEFAULT_INCLUDE),
                    "description": "Optional debug sections to include.",
                },
                "detail": {
                    "type": "string",
                    "enum": list(DEBUG_TRACE_DETAIL_LEVELS),
                    "default": "standard",
                    "description": "Controls sanitized preview length; verbose still redacts secrets and omits raw prompts.",
                },
                "eval_run_id": {
                    "type": ["string", "null"],
                    "format": "uuid",
                    "description": "Optional eval run UUID to filter eval debug artifacts for this agent.",
                },
                **_SCOPE_PARAM_PROPERTIES,
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "outputSchema": _DEBUG_TRACE_OUTPUT,
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
                    "description": (
                        "Only consider timeline events strictly newer than this cursor. The event with this exact cursor "
                        "is excluded, even when filters.message_id references it."
                    ),
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
                        "from_actor_type": {
                            "type": "string",
                            "enum": ["agent", "human_user", "external", "system"],
                            "description": (
                                "Actor source derived from the serialized timeline event: agent for outbound or peer "
                                "messages, human_user for inbound web user messages, external for other inbound "
                                "messages, and system for steps/thinking/plan events."
                            ),
                        },
                        "from_agent_id": _agent_schema(
                            "Source agent UUID for message events: the owner agent on non-peer outbound messages, "
                            "or the peer agent on inbound peer messages."
                        ),
                        "to_agent_id": _agent_schema(
                            "Target agent UUID for message events: the owner agent on non-peer inbound messages, "
                            "or the peer agent on outbound peer messages. Ordinary agent-to-human/external replies "
                            "do not have a to_agent_id."
                        ),
                        "message_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": "Timeline message UUID. Cursor strictness still applies.",
                        },
                        "peer_link_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": "Peer-link UUID for peer message events.",
                        },
                        "channel": {
                            "type": "string",
                            "description": "Message channel from the serialized timeline event, such as web, email, or sms.",
                        },
                        "status": {"type": "string", "description": "Tool-call status for steps events."},
                        "tool_name": {"type": "string", "description": "Tool name for steps events."},
                    },
                    "additionalProperties": False,
                },
                **_SCOPE_PARAM_PROPERTIES,
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
                "access": _ACCESS_METADATA_OUTPUT,
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
            "properties": {"agent_id": _agent_schema("Persistent agent UUID."), **_SCOPE_PARAM_PROPERTIES},
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
                "access": _ACCESS_METADATA_OUTPUT,
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
                **_SCOPE_PARAM_PROPERTIES,
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
                "access": _ACCESS_METADATA_OUTPUT,
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
        DEBUG_TRACE_TOOL_NAME: _tool_get_agent_debug_trace,
        "gobii_wait_for_agent_event": _tool_wait_for_agent_event,
        "gobii_list_agent_files": _tool_list_agent_files,
        "gobii_upload_agent_file": _tool_upload_agent_file,
    }[name]
    return handler(request, arguments)


@dataclass(frozen=True)
class MCPScope:
    user: object
    organization: object | None
    explicit_user_id: bool
    explicit_organization_id: bool
    admin_access: bool
    operator_user_id: object | None = None
    requested_user_id: object | None = None
    requested_organization_id: object | None = None


@dataclass(frozen=True)
class MCPAgentAccess:
    agent: PersistentAgent
    scope: MCPScope
    admin_access: bool


def _tool_list_agents(request, arguments):
    page_size = _bounded_int(arguments.get("page_size", 20), "page_size", minimum=1, maximum=100)
    page = _bounded_int(arguments.get("page", 1), "page", minimum=1, maximum=100000)
    scope = _resolve_mcp_scope(request, arguments)
    queryset = _agent_queryset_for_scope(scope)
    total = queryset.count()
    offset = (page - 1) * page_size
    agents = list(queryset[offset:offset + page_size])
    return _with_access_metadata(
        {
            "agents": [_serialize_agent(agent) for agent in agents],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_next": offset + page_size < total,
        },
        scope=scope,
    )


def _tool_get_agent(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    return _with_access_metadata(
        {"agent": _serialize_agent(access.agent)},
        access=access,
        agent=access.agent,
    )


def _tool_create_agent(request, arguments):
    scope = _resolve_mcp_scope(request, arguments)
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
    _normalize_agent_config_payload(request, payload, scope=scope)
    serializer = PersistentAgentSerializer(
        data=payload,
        context={"request": _scoped_request(request, scope), "organization": scope.organization},
    )
    try:
        serializer.is_valid(raise_exception=True)
        agent = serializer.save()
    except (DRFValidationError, DjangoValidationError) as exc:
        raise MCPToolError("Agent creation failed.", _format_validation_error(exc)) from exc

    return _with_access_metadata({"agent": _serialize_agent(agent)}, scope=scope, agent=agent)


def _tool_update_agent(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    agent = access.agent
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
    _normalize_agent_config_payload(request, payload, agent=agent, scope=access.scope)

    serializer = PersistentAgentSerializer(
        agent,
        data=payload,
        partial=True,
        context={"request": _scoped_request(request, access.scope), "organization": agent.organization},
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

    return _with_access_metadata({"agent": _serialize_agent(agent)}, access=access, agent=agent)


def _tool_archive_agent(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    agent = access.agent
    changed = agent.soft_delete()
    invalidate_account_info_cache(agent.user_id)
    return _with_access_metadata(
        {
        "status": "archived",
        "changed": changed,
        "agent": _serialize_agent(agent),
        },
        access=access,
        agent=agent,
    )


def _tool_get_agent_config_options(request, arguments):
    agent = None
    access = None
    scope = _resolve_mcp_scope(request, arguments)
    if arguments.get("agent_id"):
        access = _get_agent_access(request, arguments.get("agent_id"), arguments)
        agent = access.agent
        scope = access.scope
    owner = _agent_owner_for_request(request, agent=agent, scope=scope)
    daily_credit_options = _build_daily_credit_options(owner, getattr(agent, "preferred_llm_tier", None))
    return _with_access_metadata(
        {
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
        },
        access=access,
        scope=scope,
        agent=agent,
    )


def _tool_list_agent_links(request, arguments):
    scope = _resolve_mcp_scope(request, arguments)
    access = None
    agent_id = arguments.get("agent_id")
    if agent_id:
        access = _get_agent_access(request, agent_id, arguments)
        agent = access.agent
        links = AgentPeerLink.objects.filter(Q(agent_a=agent) | Q(agent_b=agent))
    else:
        accessible = _agent_queryset_for_scope(scope).only("id")
        links = AgentPeerLink.objects.filter(Q(agent_a__in=accessible) | Q(agent_b__in=accessible)).distinct()

    links = links.select_related("agent_a", "agent_b", "created_by").order_by("-created_at")
    if agent_id:
        links = links.filter(Q(agent_a=agent) | Q(agent_b=agent))

    return _with_access_metadata(
        {"links": [_serialize_peer_link(link) for link in links]},
        access=access,
        scope=scope,
        agent=access.agent if access else None,
    )


def _tool_link_agents(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    peer_access = _get_agent_access(request, arguments.get("peer_agent_id"), arguments)
    agent = access.agent
    peer_agent = peer_access.agent
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

    return _with_access_metadata(
        {"link": _serialize_peer_link(link), "created": created},
        access=access if access.admin_access else peer_access,
        agent=agent,
    )


def _tool_unlink_agents(request, arguments):
    link, access = _resolve_peer_link_access(request, arguments)
    payload = _serialize_peer_link(link)
    link.remove_preserving_history()
    return _with_access_metadata(
        {"status": "unlinked", "link": payload},
        access=access,
        agent=link.agent_a,
    )


def _tool_send_agent_message(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    agent = access.agent
    body = _required_string(arguments, "body", allow_blank=False)
    trigger_processing = _optional_bool(arguments.get("trigger_processing", True), "trigger_processing")
    attachment_paths = arguments.get("attachment_file_paths") or []
    if not isinstance(attachment_paths, list):
        raise MCPToolError("attachment_file_paths must be an array of filespace paths.")

    sender_user = _message_sender_user(request, access)
    sender_address = build_web_user_address(user_id=sender_user.id, agent_id=agent.id)
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
            sender_user_id=sender_user.id,
            attachments=[],
            trigger_processing=False,
        )
        create_message_attachments(message, resolved_attachments)
        if trigger_processing:
            from api.agent.tasks import process_agent_events_task

            transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.id)))

    event = serialize_message_event(message)
    cursor = event.get("cursor")
    return _with_access_metadata(
        {
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
                "user_id": sender_user.id,
            },
            "message": _serialize_message(message),
            "timeline_event": event,
            "conversation_id": str(conversation.id),
            "attachment_count": len(resolved_attachments),
        },
        access=access,
        agent=agent,
    )


def _tool_get_agent_timeline(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    agent = access.agent
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
    return _with_access_metadata(
        {
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
        },
        access=access,
        agent=agent,
    )


def _tool_get_agent_debug_trace(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    agent = access.agent
    limit = _bounded_int(
        arguments.get("limit", DEBUG_TRACE_DEFAULT_LIMIT),
        "limit",
        minimum=1,
        maximum=DEBUG_TRACE_MAX_LIMIT,
    )
    cursor = _optional_string(arguments, "cursor")
    detail = arguments.get("detail", "standard")
    if not isinstance(detail, str) or detail not in DEBUG_TRACE_DETAIL_LEVELS:
        raise MCPToolError(
            "detail must be one of summary, standard, or verbose.",
            {"field": "detail", "supported_values": list(DEBUG_TRACE_DETAIL_LEVELS)},
        )
    include = arguments.get("include", list(DEBUG_TRACE_DEFAULT_INCLUDE))
    recent_minutes = None
    recent_minutes_provided = "recent_minutes" in arguments
    if recent_minutes_provided and arguments.get("recent_minutes") is not None:
        recent_minutes = _bounded_int(
            arguments.get("recent_minutes"),
            "recent_minutes",
            minimum=1,
            maximum=DEBUG_TRACE_MAX_RECENT_MINUTES,
        )
    since = _optional_string(arguments, "since")
    until = _optional_string(arguments, "until")
    eval_run_id = None
    if arguments.get("eval_run_id"):
        eval_run_id = _parse_uuid(arguments.get("eval_run_id"), "eval_run_id")

    try:
        result = build_agent_debug_trace(
            agent,
            limit=limit,
            cursor=cursor,
            recent_minutes=recent_minutes,
            recent_minutes_provided=recent_minutes_provided,
            since=since,
            until=until,
            include=include,
            detail=detail,
            eval_run_id=eval_run_id,
        )
        return _with_access_metadata(result, access=access, agent=agent)
    except AgentDebugTraceValidationError as exc:
        raise MCPToolError(str(exc), exc.data) from exc


def _tool_wait_for_agent_event(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    agent = access.agent
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
            return _with_access_metadata(
                {
                    "matched": True,
                    "timed_out": False,
                    "events": events,
                    "next_cursor": latest_cursor,
                    "latest_cursor": latest_cursor,
                    "waited_seconds": waited_seconds,
                },
                access=access,
                agent=agent,
            )
        if time.monotonic() >= deadline:
            waited_seconds = round(time.monotonic() - start, 3)
            return _with_access_metadata(
                {
                    "matched": False,
                    "timed_out": True,
                    "events": [],
                    "next_cursor": latest_cursor,
                    "latest_cursor": latest_cursor,
                    "waited_seconds": waited_seconds,
                },
                access=access,
                agent=agent,
            )
        sleep_seconds = min(WAIT_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic()))
        if sleep_seconds:
            time.sleep(sleep_seconds)


def _tool_list_agent_files(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    agent = access.agent
    filespace = get_or_create_default_filespace(agent)
    nodes = (
        AgentFsNode.objects.alive()
        .filter(filespace=filespace)
        .only("id", "parent_id", "name", "path", "node_type", "size_bytes", "mime_type", "created_at", "updated_at")
        .order_by("parent_id", "node_type", "name")
    )
    return _with_access_metadata(
        {
            "filespace": {"id": str(filespace.id), "name": filespace.name},
            "nodes": [_serialize_file_node(node) for node in nodes],
        },
        access=access,
        agent=agent,
    )


def _tool_upload_agent_file(request, arguments):
    access = _get_agent_access(request, arguments.get("agent_id"), arguments)
    agent = access.agent
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
    return _with_access_metadata(result, access=access, agent=agent)


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


def _normalize_agent_config_payload(request, payload, *, agent=None, scope=None):
    if "schedule" in payload and payload["schedule"] == "":
        payload["schedule"] = None
    if "preferred_llm_tier" in payload:
        tier_value = payload.get("preferred_llm_tier")
        if not isinstance(tier_value, str) or not tier_value.strip():
            raise MCPToolError(
                "preferred_llm_tier must be a supported intelligence tier key.",
                {"field": "preferred_llm_tier"},
            )
        owner = _agent_owner_for_request(request, agent=agent, scope=scope)
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


def _resolve_mcp_scope(request, arguments):
    explicit_user_id = _scope_argument_supplied(arguments, "user_id")
    explicit_organization_id = _scope_argument_supplied(arguments, "organization_id")

    if (explicit_user_id or explicit_organization_id) and not _is_mcp_admin_user(request.user):
        raise MCPToolError(
            "user_id and organization_id are restricted to staff/superuser API keys.",
            {
                "code": "admin_scope_required",
                "fields": [
                    field
                    for field in ("user_id", "organization_id")
                    if _scope_argument_supplied(arguments, field)
                ],
            },
        )

    user = None
    organization = None
    requested_user_id = None
    requested_organization_id = None

    if explicit_organization_id:
        requested_organization_id = str(_parse_uuid(arguments.get("organization_id"), "organization_id"))
        organization = Organization.objects.filter(id=requested_organization_id).first()
        if organization is None:
            raise MCPToolError(
                "Organization not found.",
                {"code": "organization_not_found", "field": "organization_id"},
            )
        if not organization.is_active:
            raise MCPToolError(
                "Organization is inactive.",
                {"code": "organization_inactive", "field": "organization_id"},
            )
    elif explicit_user_id:
        organization = None
    else:
        organization = _request_organization(request)

    if explicit_user_id:
        requested_user_id = _parse_user_id(arguments.get("user_id"))
        user_model = get_user_model()
        user = user_model.objects.filter(id=requested_user_id).first()
        if user is None:
            raise MCPToolError("User not found.", {"code": "user_not_found", "field": "user_id"})
        if not user.is_active:
            raise MCPToolError("User is inactive.", {"code": "user_inactive", "field": "user_id"})
    elif organization is not None and explicit_organization_id:
        user = _default_user_for_organization(organization)
    else:
        user = request.user

    if organization is not None and explicit_user_id:
        if not OrganizationMembership.objects.filter(
            org=organization,
            user=user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).exists():
            raise MCPToolError(
                "user_id is not an active member of organization_id.",
                {
                    "code": "user_not_in_organization",
                    "field": "user_id",
                    "organization_id": str(organization.id),
                },
            )

    admin_access = bool(
        (explicit_user_id or explicit_organization_id)
        and _is_mcp_admin_user(request.user)
        and _scope_differs_from_default(request, user=user, organization=organization)
    )
    return MCPScope(
        user=user,
        organization=organization,
        explicit_user_id=explicit_user_id,
        explicit_organization_id=explicit_organization_id,
        admin_access=admin_access,
        operator_user_id=getattr(request.user, "id", None),
        requested_user_id=requested_user_id,
        requested_organization_id=requested_organization_id,
    )


def _agent_base_queryset():
    return (
        PersistentAgent.objects.non_eval()
        .alive()
        .select_related("user", "browser_use_agent", "organization", "preferred_contact_endpoint", "preferred_llm_tier")
        .order_by("-created_at")
    )


def _agent_queryset_for_scope(scope):
    if (
        scope.organization is None
        and not scope.admin_access
        and not _is_mcp_admin_user(scope.user)
        and not can_user_use_personal_agents_and_api(scope.user)
    ):
        raise MCPToolError("Personal API access requires an active trial or plan.")

    queryset = _agent_base_queryset()
    if scope.organization is not None:
        return queryset.filter(organization=scope.organization)
    return queryset.filter(user=scope.user)


def _agent_queryset(request):
    return _agent_queryset_for_scope(_resolve_mcp_scope(request, {}))


def _request_organization(request):
    auth = getattr(request, "auth", None)
    if isinstance(auth, ApiKey) and getattr(auth, "organization_id", None):
        return auth.organization
    return None


def _scope_argument_supplied(arguments, key):
    return key in arguments and arguments.get(key) not in (None, "")


def _is_mcp_admin_user(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    )


def _scope_differs_from_default(request, *, user, organization):
    default_organization = _request_organization(request)
    if default_organization is not None:
        return organization is None or organization.id != default_organization.id
    return organization is not None or getattr(user, "id", None) != getattr(request.user, "id", None)


def _parse_user_id(value):
    if isinstance(value, bool):
        raise MCPToolError("user_id must be a valid Django user id.", {"field": "user_id"})
    try:
        user_id = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise MCPToolError("user_id must be a valid Django user id.", {"field": "user_id"}) from exc
    if user_id < 1:
        raise MCPToolError("user_id must be a valid Django user id.", {"field": "user_id"})
    return user_id


def _default_user_for_organization(organization):
    if organization.created_by_id and organization.created_by.is_active:
        if OrganizationMembership.objects.filter(
            org=organization,
            user=organization.created_by,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).exists():
            return organization.created_by

    membership = (
        OrganizationMembership.objects.select_related("user")
        .filter(
            org=organization,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        .order_by("role", "user__date_joined")
        .first()
    )
    if membership and membership.user and membership.user.is_active:
        return membership.user

    raise MCPToolError(
        "Organization has no active user that can own scoped agent operations.",
        {"code": "organization_has_no_active_users", "field": "organization_id"},
    )


def _scoped_request(request, scope):
    return SimpleNamespace(user=scope.user, auth=getattr(request, "auth", None))


def _message_sender_user(request, access):
    if access.admin_access:
        return access.agent.user
    return request.user


def _with_access_metadata(payload, *, access=None, scope=None, agent=None):
    if access is not None:
        scope = access.scope
        admin_access = access.admin_access
    else:
        admin_access = bool(scope and scope.admin_access)

    if not admin_access or scope is None:
        return payload

    target_user_id = getattr(agent, "user_id", None) if agent is not None else getattr(scope.user, "id", None)
    target_organization_id = (
        getattr(agent, "organization_id", None)
        if agent is not None
        else getattr(scope.organization, "id", None)
    )
    payload["access"] = {
        "admin_access": True,
        "access_scope": "staff_cross_account",
        "operator_user_id": str(scope.operator_user_id) if scope.operator_user_id is not None else None,
        "target_user_id": str(target_user_id) if target_user_id is not None else None,
        "target_organization_id": str(target_organization_id) if target_organization_id is not None else None,
        "requested_user_id": str(scope.requested_user_id) if scope.requested_user_id is not None else None,
        "requested_organization_id": (
            str(scope.requested_organization_id) if scope.requested_organization_id is not None else None
        ),
    }
    return payload


def _agent_owner_for_request(request, *, agent=None, scope=None):
    if agent is not None:
        return agent.organization or agent.user
    if scope is not None:
        return scope.organization or scope.user
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


def _get_agent_access(request, raw_agent_id, arguments):
    agent_id = _parse_uuid(raw_agent_id, "agent_id")
    scope = _resolve_mcp_scope(request, arguments or {})
    agent = _agent_queryset_for_scope(scope).filter(id=agent_id).first()
    if agent is not None:
        return MCPAgentAccess(agent=agent, scope=scope, admin_access=scope.admin_access)

    if _is_mcp_admin_user(request.user) and not (scope.explicit_user_id or scope.explicit_organization_id):
        agent = _agent_base_queryset().filter(id=agent_id).first()
        if agent is not None:
            return MCPAgentAccess(agent=agent, scope=scope, admin_access=True)

    raise MCPToolError(
        "Agent not found or inaccessible.",
        {"code": "agent_not_found_or_inaccessible", "field": "agent_id"},
    )


def _get_agent(request, raw_agent_id):
    return _get_agent_access(request, raw_agent_id, {}).agent


def _resolve_peer_link_access(request, arguments):
    scope = _resolve_mcp_scope(request, arguments)
    accessible = _agent_queryset_for_scope(scope).only("id")
    peer_link_id = arguments.get("peer_link_id")
    if peer_link_id:
        link_id = _parse_uuid(peer_link_id, "peer_link_id")
        link = (
            AgentPeerLink.objects.filter(id=link_id)
            .filter(Q(agent_a__in=accessible) | Q(agent_b__in=accessible))
            .select_related("agent_a", "agent_b", "created_by")
            .first()
        )
        admin_access = scope.admin_access
        if link is None and _is_mcp_admin_user(request.user) and not (scope.explicit_user_id or scope.explicit_organization_id):
            link = (
                AgentPeerLink.objects.filter(id=link_id)
                .select_related("agent_a", "agent_b", "created_by")
                .first()
            )
            admin_access = link is not None
    else:
        access = _get_agent_access(request, arguments.get("agent_id"), arguments)
        peer_access = _get_agent_access(request, arguments.get("peer_agent_id"), arguments)
        agent = access.agent
        peer_agent = peer_access.agent
        pair_key = AgentPeerLink.build_pair_key(agent.id, peer_agent.id)
        link = (
            AgentPeerLink.objects.filter(pair_key=pair_key)
            .select_related("agent_a", "agent_b", "created_by")
            .first()
        )
        admin_access = access.admin_access or peer_access.admin_access

    if link is None:
        raise MCPToolError(
            "Peer link not found or inaccessible.",
            {"code": "peer_link_not_found_or_inaccessible"},
        )
    return link, MCPAgentAccess(agent=link.agent_a, scope=scope, admin_access=admin_access)


def _resolve_peer_link(request, arguments):
    return _resolve_peer_link_access(request, arguments)[0]


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


def _optional_string(arguments, key, *, allow_blank=False):
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MCPToolError(f"{key} must be a string.")
    if not allow_blank and not value.strip():
        raise MCPToolError(f"{key} cannot be blank.")
    return value if allow_blank else value.strip()


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
