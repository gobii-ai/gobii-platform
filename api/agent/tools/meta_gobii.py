"""Direct internal tools for Meta Gobii control-plane work."""

import base64
import binascii
import time
import uuid
from decimal import Decimal
from typing import Any, Callable

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from agents.services import AgentService
from api.agent.comms.message_service import inject_internal_web_message
from api.agent.core.llm_config import (
    AgentLLMTier, get_allowed_tier_rank, get_llm_tier_description, get_llm_tier_label, get_system_default_tier, resolve_intelligence_tier_for_owner, resolve_preferred_tier_for_owner,
)
from api.agent.files.attachment_helpers import AttachmentResolutionError, create_message_attachments, resolve_filespace_attachments
from api.agent.files.filespace_service import get_or_create_default_filespace, write_bytes_to_dir
from api.models import (
    AgentFsNode, AgentPeerLink, CommsAllowlistEntry, CommsAllowlistRequest, CommsChannel, PersistentAgent, PersistentAgentCommsEndpoint, PersistentAgentSystemSkillState, UserPhoneNumber,
    build_web_user_address,
)
from api.services.daily_credit_limits import calculate_daily_credit_slider_bounds, get_tier_credit_multiplier, scale_daily_credit_limit_for_tier_change
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
from api.services.persistent_agents import ensure_default_agent_email_endpoint, PersistentAgentProvisioningError, PersistentAgentProvisioningService
from console.agent_chat.timeline import (
    DEFAULT_PAGE_SIZE as TIMELINE_DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE as TIMELINE_MAX_PAGE_SIZE, fetch_timeline_window, serialize_message_event, serialize_processing_snapshot,
)
from pages.account_info_cache import invalidate_account_info_cache

from .meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY, META_GOBII_SYSTEM_SKILL_KEYS, META_GOBII_TOOL_NAMES
from .spawn_agent import execute_spawn_agent


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
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

_STRING_OR_NULL = {"type": ["string", "null"]}
_INTEGER_OR_NULL = {"type": ["integer", "null"]}
_NUMBER_OR_STRING = {"type": ["number", "string"]}
_NUMBER_STRING_OR_NULL = {"type": ["integer", "number", "string", "null"]}
_UUID = {"type": "string", "format": "uuid"}


class MetaGobiiToolError(Exception):
    def __init__(self, message: str, data: dict[str, Any] | None = None, *, status: str = "error"):
        super().__init__(message)
        self.message = message
        self.data = data or {}
        self.status = status


def _object(properties: dict[str, Any], *, required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _output_object(properties: dict[str, Any], *, required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema = _object(properties, required=required)
    schema["additionalProperties"] = True
    return schema


def _array(item_schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item_schema}


def _agent_id(description: str = "Persistent agent UUID.") -> dict[str, Any]:
    return {"type": "string", "format": "uuid", "description": description}


_AGENT_REF_OUTPUT = _output_object(
    {
        "id": _UUID,
        "name": {"type": "string"},
        "is_active": {"type": "boolean"},
        "life_state": {"type": "string"},
        "is_deleted": {"type": "boolean"},
    },
    required=("id", "name"),
)

_AGENT_OUTPUT = _output_object(
    {
        "id": _UUID,
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
        "is_deleted": {"type": "boolean"},
        "deleted_at": _STRING_OR_NULL,
    },
    required=("id", "name", "schedule", "is_active"),
)

_LINK_OUTPUT = _output_object(
    {
        "id": _UUID,
        "agent_a": _AGENT_REF_OUTPUT,
        "agent_b": _AGENT_REF_OUTPUT,
        "is_enabled": {"type": "boolean"},
        "messages_per_window": {"type": "integer"},
        "window_hours": {"type": "integer"},
        "feature_flag": _STRING_OR_NULL,
        "pair_key": {"type": "string"},
        "created_by_user_id": {"type": ["integer", "string", "null"]},
        "created_at": _STRING_OR_NULL,
        "updated_at": _STRING_OR_NULL,
    },
    required=("id", "agent_a", "agent_b", "is_enabled"),
)

_MESSAGE_OUTPUT = _output_object(
    {
        "id": _UUID,
        "owner_agent_id": _STRING_OR_NULL,
        "conversation_id": _STRING_OR_NULL,
        "is_outbound": {"type": "boolean"},
        "body": {"type": "string"},
        "timestamp": _STRING_OR_NULL,
    },
    required=("id", "body"),
)

_FILE_NODE_OUTPUT = _output_object(
    {
        "id": _UUID,
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

_CONTACT_OUTPUT = _output_object(
    {
        "id": _UUID,
        "agent_id": _UUID,
        "channel": {"type": "string"},
        "address": {"type": "string"},
        "is_active": {"type": "boolean"},
        "allow_inbound": {"type": "boolean"},
        "allow_outbound": {"type": "boolean"},
        "can_configure": {"type": "boolean"},
        "created_at": _STRING_OR_NULL,
        "updated_at": _STRING_OR_NULL,
    },
    required=("id", "agent_id", "channel", "address"),
)

_CONTACT_REQUEST_OUTPUT = _output_object(
    {
        "id": _UUID,
        "agent_id": _UUID,
        "channel": {"type": "string"},
        "address": {"type": "string"},
        "name": _STRING_OR_NULL,
        "reason": _STRING_OR_NULL,
        "purpose": _STRING_OR_NULL,
        "request_inbound": {"type": "boolean"},
        "request_outbound": {"type": "boolean"},
        "request_configure": {"type": "boolean"},
        "status": {"type": "string"},
        "requested_at": _STRING_OR_NULL,
        "responded_at": _STRING_OR_NULL,
        "expires_at": _STRING_OR_NULL,
    },
    required=("id", "agent_id", "channel", "address", "status"),
)

_ENDPOINT_OUTPUT = _output_object(
    {
        "id": _UUID,
        "owner_agent_id": _STRING_OR_NULL,
        "channel": {"type": "string"},
        "address": {"type": "string"},
        "is_primary": {"type": "boolean"},
        "is_preferred": {"type": "boolean"},
        "safe_for_preferred_contact": {"type": "boolean"},
    },
    required=("id", "channel", "address"),
)


def _confirmation_description(action: str) -> str:
    return (
        f"Set true only after the human explicitly approved this Meta Gobii {action}. "
        "If not confirmed, ask for approval before calling this mutating tool."
    )


def _confirmation_output_schema() -> dict[str, Any]:
    return {
        "confirmation_prompt": {"type": "string"},
        "proposed_actions": _array({"type": "string"}),
        "requires_user_confirmed": {"type": "boolean"},
    }


TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "meta_gobii_list_agents": {
        "description": "List persistent Gobiis in this Gobii's personal owner scope or organization scope. Read-only.",
        "parameters": _object(
            {
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "include_archived": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include soft-deleted archived agents in the same accessible owner scope.",
                },
            }
        ),
        "output": _output_object(
            {
                "status": {"type": "string"},
                "agents": _array(_AGENT_OUTPUT),
                "page": {"type": "integer"},
                "page_size": {"type": "integer"},
                "total": {"type": "integer"},
                "has_next": {"type": "boolean"},
            },
            required=("status", "agents", "page", "page_size", "total", "has_next"),
        ),
    },
    "meta_gobii_get_agent": {
        "description": "Retrieve one accessible persistent Gobii by id. Read-only.",
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "include_archived": {"type": "boolean", "default": False},
            },
            required=("agent_id",),
        ),
        "output": _output_object({"status": {"type": "string"}, "agent": _AGENT_OUTPUT}, required=("status", "agent")),
    },
    "meta_gobii_create_agent": {
        "description": (
            "Create a persistent Gobii in this Gobii's same owner or organization scope using internal provisioning. "
            "Requires human approval via user_confirmed before changing the Gobii control plane. "
            "Use for explicitly requested team/graph creation, not speculative extra workers."
        ),
        "parameters": _object(
            {
                "name": {"type": "string", "description": "Optional display name. Gobii generates one when omitted."},
                "charter": {"type": "string", "description": "Instructions describing the agent's job."},
                "schedule": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional cron-like schedule, @daily, or @every interval. Omit unless the user explicitly "
                        "asked for recurring, scheduled, ongoing, proactive, digest, watch, check-in, or cadence-based "
                        "behavior and the approval scope includes that schedule."
                    ),
                },
                "is_active": {"type": "boolean", "default": True},
                "preferred_llm_tier": {
                    "type": "string",
                    "description": "Preferred intelligence tier key such as standard, premium, max, ultra, or ultra_max.",
                },
                "daily_credit_limit": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Soft daily credit target. Null means unlimited. Ask for confirmation before raising broad limits.",
                },
                "whitelist_policy": {"type": "string", "enum": ["default", "manual"]},
                "proactive_opt_in": {"type": "boolean"},
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("create"),
                },
            }
        ),
        "output": _output_object(
            {"status": {"type": "string"}, "agent": _AGENT_OUTPUT, **_confirmation_output_schema()},
            required=("status",),
        ),
    },
    "meta_gobii_request_agent_creation": {
        "description": (
            "Request creation of a specialist Gobii through the existing human Create/Decline approval flow. "
            "This creates an AgentSpawnRequest only; it does not create the Gobii until a human approves it. "
            "Use this as the Meta Gobii-gated replacement for legacy spawn_agent handoffs."
        ),
        "parameters": _object(
            {
                "charter": {
                    "type": "string",
                    "description": "Full charter/instructions for the requested specialist Gobii.",
                },
                "handoff_message": {
                    "type": "string",
                    "description": "Initial task handoff sent to the requested Gobii after approval.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this request needs a specialist Gobii rather than the invoking Gobii.",
                },
                "will_continue_work": {
                    "type": "boolean",
                    "description": "true = continue with more work now; false = done after creating the approval request.",
                },
            },
            required=("charter", "handoff_message", "will_continue_work"),
        ),
        "output": _output_object(
            {
                "status": {"type": "string"},
                "request_status": {"type": "string"},
                "message": {"type": "string"},
                "created_count": {"type": "integer"},
                "already_pending_count": {"type": "integer"},
                "spawn_request_id": _UUID,
                "approval_url": _STRING_OR_NULL,
                "decision_api_url": _STRING_OR_NULL,
                "auto_sleep_ok": {"type": "boolean"},
            },
            required=("status",),
        ),
    },
    "meta_gobii_update_agent": {
        "description": (
            "Update mutable settings on one accessible Gobii. Requires human approval via user_confirmed before changing "
            "name, charter, schedule, active state, resource limits, intelligence tier, allowlist policy, or proactivity."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "name": {"type": "string"},
                "charter": {"type": "string"},
                "schedule": {
                    "type": ["string", "null"],
                    "description": (
                        "Set or clear only when the user explicitly asked to create, change, or remove this Gobii's "
                        "schedule and the approval scope includes the schedule action."
                    ),
                },
                "is_active": {"type": "boolean"},
                "preferred_llm_tier": {
                    "type": "string",
                    "description": "Preferred intelligence tier key such as standard, premium, max, ultra, or ultra_max.",
                },
                "daily_credit_limit": {"type": ["integer", "null"], "minimum": 1},
                "whitelist_policy": {"type": "string", "enum": ["default", "manual"]},
                "proactive_opt_in": {"type": "boolean"},
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("update"),
                },
            },
            required=("agent_id",),
        ),
        "output": _output_object(
            {
                "status": {"type": "string"},
                "message": {"type": "string"},
                "agent": _AGENT_OUTPUT,
                "changed_fields": _array({"type": "string"}),
                **_confirmation_output_schema(),
            },
            required=("status",),
        ),
    },
    "meta_gobii_archive_agent": {
        "description": (
            "Soft-archive one accessible Gobii using normal reversible archive behavior. "
            "Never archive agents in bulk unless the human explicitly confirmed the exact scope."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("archive"),
                },
            },
            required=("agent_id",),
        ),
        "output": _output_object(
            {
                "status": {"type": "string"},
                "message": {"type": "string"},
                "changed": {"type": "boolean"},
                "agent": _AGENT_OUTPUT,
                **_confirmation_output_schema(),
            },
            required=("status",),
        ),
    },
    "meta_gobii_get_agent_config_options": {
        "description": "Discover supported agent settings, allowed intelligence tiers, credit-limit bounds, schedules, and policies. Read-only.",
        "parameters": _object({"agent_id": _agent_id("Optional agent UUID to include current config.")}),
        "output": _output_object({}),
    },
    "meta_gobii_list_agent_links": {
        "description": "List peer-agent links involving accessible agents. Read-only.",
        "parameters": _object({"agent_id": _agent_id("Optional agent UUID to filter links.")}),
        "output": _output_object({"status": {"type": "string"}, "links": _array(_LINK_OUTPUT)}, required=("status", "links")),
    },
    "meta_gobii_link_agents": {
        "description": (
            "Create or enable a peer-agent link between two accessible Gobiis so they can coordinate. "
            "Requires human approval via user_confirmed before changing graph wiring. "
            "Use only for user-requested team/graph wiring."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id("First persistent agent UUID."),
                "peer_agent_id": _agent_id("Second persistent agent UUID."),
                "messages_per_window": {"type": "integer", "minimum": 1, "maximum": 500, "default": 30},
                "window_hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 6},
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("link"),
                },
            },
            required=("agent_id", "peer_agent_id"),
        ),
        "output": _output_object(
            {"status": {"type": "string"}, "link": _LINK_OUTPUT, "created": {"type": "boolean"}, **_confirmation_output_schema()},
            required=("status",),
        ),
    },
    "meta_gobii_unlink_agents": {
        "description": (
            "Remove one peer-agent link while preserving historical peer conversation messages. "
            "Ask for confirmation before unlinking or rewriting a graph."
        ),
        "parameters": _object(
            {
                "peer_link_id": {"type": "string", "format": "uuid", "description": "Existing peer link UUID."},
                "agent_id": _agent_id("First persistent agent UUID when peer_link_id is omitted."),
                "peer_agent_id": _agent_id("Second persistent agent UUID when peer_link_id is omitted."),
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("unlink"),
                },
            }
        ),
        "output": _output_object(
            {"status": {"type": "string"}, "message": {"type": "string"}, "link": _LINK_OUTPUT, **_confirmation_output_schema()}
        ),
    },
    "meta_gobii_send_agent_message": {
        "description": (
            "Send a briefing or task message to one accessible Gobii and optionally attach files already in that agent's filespace. "
            "Requires human approval via user_confirmed before messaging or briefing another Gobii."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id(),
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
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("message or briefing"),
                },
            },
            required=("agent_id", "body"),
        ),
        "output": _output_object(
            {
                "status": {"type": "string"},
                "message_id": _UUID,
                "agent_id": _UUID,
                "cursor": _STRING_OR_NULL,
                "latest_cursor": _STRING_OR_NULL,
                "created_at": _STRING_OR_NULL,
                "message": _MESSAGE_OUTPUT,
                "conversation_id": _UUID,
                "attachment_count": {"type": "integer"},
                **_confirmation_output_schema(),
            },
            required=("status",),
        ),
    },
    "meta_gobii_get_agent_timeline": {
        "description": "Fetch recent chat, task, thinking, and processing events for one accessible Gobii. Read-only.",
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "after_cursor": {
                    "type": ["string", "null"],
                    "description": "Return events strictly newer than this durable timeline cursor.",
                },
                "cursor": {"type": ["string", "null"], "description": "Cursor from a previous timeline result."},
                "direction": {"type": "string", "enum": ["initial", "older", "newer"], "default": "initial"},
                "limit": {"type": "integer", "minimum": 1, "maximum": TIMELINE_MAX_PAGE_SIZE, "default": TIMELINE_DEFAULT_PAGE_SIZE},
            },
            required=("agent_id",),
        ),
        "output": _output_object({}),
    },
    "meta_gobii_wait_for_agent_event": {
        "description": "Bounded long-poll over an accessible Gobii's unified timeline using durable cursors and structured filters. Read-only.",
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "after_cursor": {"type": ["string", "null"]},
                "timeout_seconds": {"type": "integer", "minimum": 0, "maximum": WAIT_MAX_TIMEOUT_SECONDS, "default": WAIT_DEFAULT_TIMEOUT_SECONDS},
                "limit": {"type": "integer", "minimum": 1, "maximum": TIMELINE_MAX_PAGE_SIZE, "default": 20},
                "event_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(WAIT_EVENT_TYPES)},
                },
                "filters": {
                    "type": "object",
                    "properties": {
                        "from_actor_type": {"type": "string", "enum": ["agent", "human_user", "external", "system"]},
                        "from_agent_id": _agent_id("Source agent UUID for message events."),
                        "to_agent_id": _agent_id("Target agent UUID for message events."),
                        "message_id": {"type": "string", "format": "uuid"},
                        "peer_link_id": {"type": "string", "format": "uuid"},
                        "channel": {"type": "string"},
                        "status": {"type": "string"},
                        "tool_name": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            required=("agent_id",),
        ),
        "output": _output_object({}),
    },
    "meta_gobii_list_agent_files": {
        "description": "List files and folders in one accessible Gobii's default filespace. Read-only.",
        "parameters": _object({"agent_id": _agent_id()}, required=("agent_id",)),
        "output": _output_object(
            {
                "status": {"type": "string"},
                "filespace": _output_object({"id": _UUID, "name": {"type": "string"}}, required=("id", "name")),
                "nodes": _array(_FILE_NODE_OUTPUT),
            },
            required=("status", "filespace", "nodes"),
        ),
    },
    "meta_gobii_upload_agent_file": {
        "description": (
            "Upload a small base64-encoded file into one accessible Gobii's filespace for later use or message attachment. "
            "Requires human approval via user_confirmed before writing files."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "path": {"type": "string", "description": "Filespace path, e.g. /uploads/report.txt."},
                "content_base64": {"type": "string", "description": "Base64-encoded file content up to 5 MiB."},
                "mime_type": {"type": "string", "default": "application/octet-stream"},
                "overwrite": {"type": "boolean", "default": False},
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("file upload"),
                },
            },
            required=("agent_id", "path", "content_base64"),
        ),
        "output": _output_object(_confirmation_output_schema()),
    },
    "meta_gobii_list_contacts": {
        "description": "List manual allowlist contacts for one accessible Gobii. Read-only.",
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "include_inactive": {"type": "boolean", "default": False},
            },
            required=("agent_id",),
        ),
        "output": _output_object({"status": {"type": "string"}, "contacts": _array(_CONTACT_OUTPUT)}, required=("status", "contacts")),
    },
    "meta_gobii_add_contact": {
        "description": (
            "Add or reactivate one manual allowlist contact for an accessible Gobii. "
            "Requires human approval via user_confirmed before changing contacts. "
            "Only use addresses the user supplied, approved, or that are already known internal team contacts."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "channel": {"type": "string", "enum": ["email", "sms", "web"]},
                "address": {"type": "string"},
                "allow_inbound": {"type": "boolean", "default": True},
                "allow_outbound": {"type": "boolean", "default": True},
                "can_configure": {
                    "type": "boolean",
                    "default": False,
                    "description": "Grant only to owner-approved contacts who may change charter or schedule.",
                },
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("contact addition"),
                },
            },
            required=("agent_id", "channel", "address"),
        ),
        "output": _output_object(
            {"status": {"type": "string"}, "contact": _CONTACT_OUTPUT, "created": {"type": "boolean"}, **_confirmation_output_schema()}
        ),
    },
    "meta_gobii_remove_contact": {
        "description": (
            "Deactivate one manual allowlist contact for an accessible Gobii. "
            "This is reversible by adding the same contact again. Ask for confirmation first."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "contact_id": {"type": "string", "format": "uuid"},
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("contact removal"),
                },
            },
            required=("agent_id", "contact_id"),
        ),
        "output": _output_object(
            {"status": {"type": "string"}, "message": {"type": "string"}, "contact": _CONTACT_OUTPUT, **_confirmation_output_schema()}
        ),
    },
    "meta_gobii_list_pending_contacts": {
        "description": "List pending contact permission requests for one accessible Gobii. Read-only.",
        "parameters": _object({"agent_id": _agent_id()}, required=("agent_id",)),
        "output": _output_object(
            {"status": {"type": "string"}, "requests": _array(_CONTACT_REQUEST_OUTPUT)},
            required=("status", "requests"),
        ),
    },
    "meta_gobii_approve_pending_contact": {
        "description": (
            "Approve or reject one pending contact request for an accessible Gobii using the normal allowlist-request flow. "
            "Requires human approval via user_confirmed before approving or rejecting contact access."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "request_id": {"type": "string", "format": "uuid"},
                "decision": {"type": "string", "enum": ["approve", "reject"], "default": "approve"},
                "allow_inbound": {"type": "boolean", "default": True},
                "allow_outbound": {"type": "boolean", "default": True},
                "can_configure": {"type": "boolean", "default": False},
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("pending contact decision"),
                },
            },
            required=("agent_id", "request_id"),
        ),
        "output": _output_object(
            {
                "status": {"type": "string"},
                "request": _CONTACT_REQUEST_OUTPUT,
                "contact": _CONTACT_OUTPUT,
                **_confirmation_output_schema(),
            },
            required=("status",),
        ),
    },
    "meta_gobii_list_contact_endpoints": {
        "description": "List agent-owned contact endpoints and the current preferred owner-safe endpoint for one accessible Gobii. Read-only.",
        "parameters": _object({"agent_id": _agent_id()}, required=("agent_id",)),
        "output": _output_object({"status": {"type": "string"}, "endpoints": _array(_ENDPOINT_OUTPUT)}, required=("status", "endpoints")),
    },
    "meta_gobii_set_preferred_contact_endpoint": {
        "description": (
            "Set or clear one accessible Gobii's preferred owner-safe contact endpoint. "
            "Requires human approval via user_confirmed before changing preferred contact routing. "
            "Only agent-owned endpoints or validated owner email/SMS endpoints are accepted."
        ),
        "parameters": _object(
            {
                "agent_id": _agent_id(),
                "endpoint_id": {"type": "string", "format": "uuid"},
                "channel": {"type": "string", "enum": ["email", "sms"]},
                "clear": {"type": "boolean", "default": False},
                "user_confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": _confirmation_description("preferred contact endpoint change"),
                },
            },
            required=("agent_id",),
        ),
        "output": _output_object(
            {
                "status": {"type": "string"},
                "agent": _AGENT_OUTPUT,
                "preferred_contact_endpoint": {"anyOf": [_ENDPOINT_OUTPUT, {"type": "null"}]},
                **_confirmation_output_schema(),
            },
            required=("status",),
        ),
    },
}


def is_meta_gobii_available_for_agent(agent: PersistentAgent | None) -> bool:
    return agent is not None


def get_meta_gobii_tool_definition(tool_name: str) -> dict[str, Any]:
    definition = TOOL_DEFINITIONS[tool_name]
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": definition["description"],
            "parameters": definition["parameters"],
        },
    }


def execute_meta_gobii_tool(agent: PersistentAgent, tool_name: str, params: dict[str, Any] | None) -> dict[str, Any]:
    if tool_name not in META_GOBII_TOOL_NAMES:
        return {"status": "error", "message": f"Unknown Meta Gobii tool: {tool_name}"}
    if not _meta_gobii_skill_enabled(agent):
        return {
            "status": "error",
            "message": (
                "Meta Gobii tools are available only after enabling the "
                f"{META_GOBII_SYSTEM_SKILL_KEY} system skill for this agent."
            ),
        }

    handler = _HANDLERS[tool_name]
    try:
        return handler(agent, params or {})
    except MetaGobiiToolError as exc:
        payload = {"status": exc.status, "message": exc.message}
        if exc.data:
            if exc.status == "confirmation_required":
                payload.update(exc.data)
            else:
                payload["details"] = exc.data
        return payload
    except (DjangoValidationError, IntegrityError, PersistentAgentProvisioningError) as exc:
        return {"status": "error", "message": "Meta Gobii tool failed validation.", "details": _format_validation_error(exc)}


def _meta_gobii_skill_enabled(agent: PersistentAgent) -> bool:
    return PersistentAgentSystemSkillState.objects.filter(
        agent=agent,
        skill_key__in=META_GOBII_SYSTEM_SKILL_KEYS,
        is_enabled=True,
    ).exists()


def _require_user_confirmed(params: dict[str, Any], *, action: str, proposed_actions: list[str]) -> None:
    if _optional_bool(params.get("user_confirmed", False), "user_confirmed"):
        return
    prompt = "Please confirm Meta Gobii should " + action.rstrip(".") + "."
    raise MetaGobiiToolError(
        f"Human confirmation is required before Meta Gobii can {action.rstrip('.')}.",
        {
            "confirmation_prompt": prompt,
            "proposed_actions": proposed_actions,
            "requires_user_confirmed": True,
        },
        status="confirmation_required",
    )


def _owner_for_agent(agent: PersistentAgent):
    return agent.organization if agent.organization_id else agent.user


def _agent_queryset(invoking_agent: PersistentAgent, *, include_archived: bool = False):
    queryset = (
        PersistentAgent.objects.non_eval()
        .select_related("browser_use_agent", "organization", "preferred_contact_endpoint", "preferred_llm_tier")
        .order_by("-created_at")
    )
    if not include_archived:
        queryset = queryset.alive()
    if invoking_agent.organization_id:
        return queryset.filter(organization_id=invoking_agent.organization_id)
    return queryset.filter(user_id=invoking_agent.user_id, organization__isnull=True)


def _get_agent(invoking_agent: PersistentAgent, raw_agent_id: Any, *, include_archived: bool = False) -> PersistentAgent:
    agent_id = _parse_uuid(raw_agent_id, "agent_id")
    agent = _agent_queryset(invoking_agent, include_archived=include_archived).filter(id=agent_id).first()
    if agent is None:
        raise MetaGobiiToolError("Agent not found or inaccessible.")
    return agent


def _tool_list_agents(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    page_size = _bounded_int(params.get("page_size", 20), "page_size", minimum=1, maximum=100)
    page = _bounded_int(params.get("page", 1), "page", minimum=1, maximum=100000)
    include_archived = _optional_bool(params.get("include_archived", False), "include_archived")
    queryset = _agent_queryset(invoking_agent, include_archived=include_archived)
    total = queryset.count()
    offset = (page - 1) * page_size
    agents = list(queryset[offset:offset + page_size])
    return {
        "status": "ok",
        "agents": [_serialize_agent(agent) for agent in agents],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": offset + page_size < total,
    }


def _tool_get_agent(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    include_archived = _optional_bool(params.get("include_archived", False), "include_archived")
    return {"status": "ok", "agent": _serialize_agent(_get_agent(invoking_agent, params.get("agent_id"), include_archived=include_archived))}


def _tool_create_agent(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    proposed_actions = ["Create one persistent Gobii in the invoking Gobii's same owner or organization scope."]
    if params.get("name"):
        proposed_actions.append(f"Name it `{params.get('name')}`.")
    if params.get("charter"):
        proposed_actions.append("Set the requested charter/instructions.")
    if "schedule" in params:
        proposed_actions.append("Set the requested schedule.")
    if "preferred_llm_tier" in params:
        proposed_actions.append("Set the requested intelligence tier.")
    if "daily_credit_limit" in params:
        proposed_actions.append("Set the requested daily credit/resource limit.")
    _require_user_confirmed(
        params,
        action="create a new persistent Gobii in this owner scope",
        proposed_actions=proposed_actions,
    )

    owner = _owner_for_agent(invoking_agent)
    if not AgentService.has_agents_available(owner):
        raise MetaGobiiToolError("No additional agent capacity is available for this owner scope.")

    preferred_tier = None
    requested_tier = params.get("preferred_llm_tier")
    if requested_tier not in (None, ""):
        preferred_tier = _resolve_requested_tier_or_error(owner, requested_tier)

    daily_credit_limit = None
    if "daily_credit_limit" in params:
        daily_credit_limit = _normalize_daily_credit_limit(params.get("daily_credit_limit"))

    schedule = params.get("schedule")
    if schedule == "":
        schedule = None

    with transaction.atomic():
        try:
            result = PersistentAgentProvisioningService.provision(
                user=invoking_agent.user,
                organization=invoking_agent.organization,
                name=_optional_string(params.get("name"), "name"),
                charter=_optional_string(params.get("charter"), "charter") or "",
                schedule=schedule,
                is_active=_optional_bool(params.get("is_active", True), "is_active"),
                whitelist_policy=_optional_choice(
                    params.get("whitelist_policy"),
                    "whitelist_policy",
                    {choice[0] for choice in PersistentAgent.WhitelistPolicy.choices},
                    allow_missing=True,
                ),
                preferred_llm_tier=preferred_tier,
                planning_state=PersistentAgent.PlanningState.SKIPPED,
            )
        except PersistentAgentProvisioningError:
            raise

        agent = result.agent
        ensure_default_agent_email_endpoint(agent, is_primary=True)

        update_fields: list[str] = []
        if "daily_credit_limit" in params and agent.daily_credit_limit != daily_credit_limit:
            agent.daily_credit_limit = daily_credit_limit
            update_fields.append("daily_credit_limit")
        if "proactive_opt_in" in params:
            proactive_opt_in = _optional_bool(params.get("proactive_opt_in"), "proactive_opt_in")
            if agent.proactive_opt_in != proactive_opt_in:
                agent.proactive_opt_in = proactive_opt_in
                update_fields.append("proactive_opt_in")
        if update_fields:
            agent.full_clean()
            agent.save(update_fields=update_fields)

        def _queue_initial_processing() -> None:
            from api.agent.tasks import process_agent_events_task

            process_agent_events_task.delay(str(agent.id))

        transaction.on_commit(_queue_initial_processing)
    return {"status": "ok", "agent": _serialize_agent(agent)}


def _tool_request_agent_creation(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    return execute_spawn_agent(invoking_agent, params, invoked_via_meta_gobii=True)


def _tool_update_agent(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    mutable_fields = {
        "name",
        "charter",
        "schedule",
        "is_active",
        "preferred_llm_tier",
        "daily_credit_limit",
        "whitelist_policy",
        "proactive_opt_in",
    }
    requested_fields = {field for field in mutable_fields if field in params}
    if not requested_fields:
        raise MetaGobiiToolError("At least one mutable field is required.")

    _require_user_confirmed(
        params,
        action=f"update `{agent.name}`",
        proposed_actions=[f"Update {', '.join(sorted(requested_fields))} on `{agent.name}`."],
    )

    previous_daily_credit_limit = agent.daily_credit_limit
    previous_tier_id = agent.preferred_llm_tier_id
    previous_tier = agent.preferred_llm_tier
    previous_tier_key = getattr(getattr(agent, "preferred_llm_tier", None), "key", "standard")
    changed_fields: set[str] = set()
    browser_name_changed = False

    if "name" in params:
        name = _required_string(params, "name", allow_blank=False)
        if agent.name != name:
            agent.name = name
            changed_fields.add("name")
            if agent.browser_use_agent and agent.browser_use_agent.name != name:
                agent.browser_use_agent.name = name
                browser_name_changed = True
    if "charter" in params:
        charter = _required_string(params, "charter", allow_blank=True)
        if agent.charter != charter:
            agent.charter = charter
            changed_fields.add("charter")
    if "schedule" in params:
        schedule = params.get("schedule")
        if schedule == "":
            schedule = None
        if schedule is not None and not isinstance(schedule, str):
            raise MetaGobiiToolError("schedule must be a string or null.")
        if agent.schedule != schedule:
            agent.schedule = schedule
            changed_fields.add("schedule")
    if "is_active" in params:
        is_active = _optional_bool(params.get("is_active"), "is_active")
        if agent.is_active != is_active:
            agent.is_active = is_active
            changed_fields.add("is_active")
    if "preferred_llm_tier" in params:
        tier = _resolve_requested_tier_or_error(_owner_for_agent(agent), params.get("preferred_llm_tier"))
        if agent.preferred_llm_tier_id != tier.id:
            agent.preferred_llm_tier = tier
            changed_fields.add("preferred_llm_tier")
            if "daily_credit_limit" not in params:
                owner = agent.organization or agent.user
                credit_settings = get_daily_credit_settings_for_owner(owner)
                new_tier_multiplier = get_tier_credit_multiplier(tier)
                slider_bounds = calculate_daily_credit_slider_bounds(
                    credit_settings,
                    tier_multiplier=new_tier_multiplier,
                )
                scaled_limit = scale_daily_credit_limit_for_tier_change(
                    agent.daily_credit_limit,
                    from_multiplier=get_tier_credit_multiplier(previous_tier),
                    to_multiplier=new_tier_multiplier,
                    slider_min=slider_bounds["slider_min"],
                    slider_max=slider_bounds["slider_limit_max"],
                )
                if agent.daily_credit_limit != scaled_limit:
                    agent.daily_credit_limit = scaled_limit
                    changed_fields.add("daily_credit_limit")
    if "daily_credit_limit" in params:
        daily_credit_limit = _normalize_daily_credit_limit(params.get("daily_credit_limit"))
        if agent.daily_credit_limit != daily_credit_limit:
            agent.daily_credit_limit = daily_credit_limit
            changed_fields.add("daily_credit_limit")
    if "whitelist_policy" in params:
        policy = _optional_choice(
            params.get("whitelist_policy"),
            "whitelist_policy",
            {choice[0] for choice in PersistentAgent.WhitelistPolicy.choices},
            allow_missing=False,
        )
        if agent.whitelist_policy != policy:
            agent.whitelist_policy = policy
            changed_fields.add("whitelist_policy")
    if "proactive_opt_in" in params:
        proactive_opt_in = _optional_bool(params.get("proactive_opt_in"), "proactive_opt_in")
        if agent.proactive_opt_in != proactive_opt_in:
            agent.proactive_opt_in = proactive_opt_in
            changed_fields.add("proactive_opt_in")

    if changed_fields:
        if "schedule" not in changed_fields:
            agent._skip_unchanged_schedule_validation = True
        try:
            agent.full_clean()
        finally:
            if hasattr(agent, "_skip_unchanged_schedule_validation"):
                delattr(agent, "_skip_unchanged_schedule_validation")
        with transaction.atomic():
            agent.save(update_fields=list(changed_fields))
            if browser_name_changed:
                agent.browser_use_agent.save(update_fields=["name"])
        if "charter" in changed_fields:
            _schedule_charter_artifacts_on_commit(agent)
        _queue_agent_settings_resume_if_needed(
            agent,
            previous_daily_credit_limit=previous_daily_credit_limit,
            previous_tier_id=previous_tier_id,
            previous_tier_key=previous_tier_key,
        )

    message = "Agent updated." if changed_fields else "No changes were needed."
    return {
        "status": "ok",
        "message": message,
        "agent": _serialize_agent(agent),
        "changed_fields": sorted(changed_fields),
    }


def _tool_archive_agent(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    _require_user_confirmed(
        params,
        action=f"archive `{agent.name}`",
        proposed_actions=[f"Soft-archive `{agent.name}`. This can be restored from the owner scope."],
    )
    changed = agent.soft_delete()
    invalidate_account_info_cache(invoking_agent.user_id)
    return {
        "status": "archived",
        "message": "Agent was soft-archived and can be restored from the owner scope.",
        "changed": changed,
        "agent": _serialize_agent(agent),
    }


def _tool_get_agent_config_options(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = None
    if params.get("agent_id"):
        agent = _get_agent(invoking_agent, params.get("agent_id"))
    owner = _owner_for_agent(agent or invoking_agent)
    return {
        "status": "ok",
        "owner": _serialize_owner_ref(owner),
        "agent": _serialize_agent(agent) if agent else None,
        "capacity": {
            "agents_available": AgentService.get_agents_available(owner),
            "agents_in_use": AgentService.get_agents_in_use(owner),
        },
        "fields": {
            "preferred_llm_tier": _build_intelligence_options(owner),
            "daily_credit_limit": _build_daily_credit_options(owner, getattr(agent, "preferred_llm_tier", None)),
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
            "proactive_opt_in": {"type": "boolean"},
        },
        "unsupported_mcp_equivalent_fields": [
            "arbitrary_url_file_fetch",
            "ad_hoc_runtime_session",
            "separate_task_or_run_abstraction",
        ],
    }


def _tool_list_agent_links(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    accessible = _agent_queryset(invoking_agent).only("id")
    links = (
        AgentPeerLink.objects.filter(Q(agent_a__in=accessible) | Q(agent_b__in=accessible))
        .select_related("agent_a", "agent_b", "created_by")
        .distinct()
        .order_by("-created_at")
    )
    if params.get("agent_id"):
        agent = _get_agent(invoking_agent, params.get("agent_id"))
        links = links.filter(Q(agent_a=agent) | Q(agent_b=agent))
    return {"status": "ok", "links": [_serialize_peer_link(link) for link in links]}


def _tool_link_agents(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    peer_agent = _get_agent(invoking_agent, params.get("peer_agent_id"))
    if agent.id == peer_agent.id:
        raise MetaGobiiToolError("Cannot link an agent to itself.")
    messages_per_window = _bounded_int(params.get("messages_per_window", 30), "messages_per_window", minimum=1, maximum=500)
    window_hours = _bounded_int(params.get("window_hours", 6), "window_hours", minimum=1, maximum=168)
    _require_user_confirmed(
        params,
        action=f"link `{agent.name}` and `{peer_agent.name}`",
        proposed_actions=[
            f"Create or enable a peer-agent link between `{agent.name}` and `{peer_agent.name}`.",
            f"Set message window to {messages_per_window} messages per {window_hours} hours.",
        ],
    )
    pair_key = AgentPeerLink.build_pair_key(agent.id, peer_agent.id)
    link = AgentPeerLink.objects.filter(pair_key=pair_key).first()
    created = False
    if link is None:
        link = AgentPeerLink(
            agent_a=agent,
            agent_b=peer_agent,
            created_by=invoking_agent.user,
            messages_per_window=messages_per_window,
            window_hours=window_hours,
            is_enabled=True,
        )
        created = True
    else:
        link.messages_per_window = messages_per_window
        link.window_hours = window_hours
        link.is_enabled = True
    link.save()
    return {"status": "ok", "link": _serialize_peer_link(link), "created": created}


def _tool_unlink_agents(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    link = _resolve_peer_link(invoking_agent, params)
    _require_user_confirmed(
        params,
        action=f"unlink `{link.agent_a.name}` and `{link.agent_b.name}`",
        proposed_actions=[
            f"Remove the peer-agent link between `{link.agent_a.name}` and `{link.agent_b.name}`.",
            "Preserve historical peer conversation messages.",
        ],
    )
    payload = _serialize_peer_link(link)
    link.remove_preserving_history()
    return {"status": "unlinked", "message": "Peer link removed; historical conversation messages were preserved.", "link": payload}


def _tool_send_agent_message(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    body = _required_string(params, "body", allow_blank=False)
    trigger_processing = _optional_bool(params.get("trigger_processing", True), "trigger_processing")
    attachment_paths = params.get("attachment_file_paths") or []
    if not isinstance(attachment_paths, list) or any(not isinstance(path, str) for path in attachment_paths):
        raise MetaGobiiToolError("attachment_file_paths must be an array of filespace paths.")
    _require_user_confirmed(
        params,
        action=f"message `{agent.name}`",
        proposed_actions=[
            f"Send a briefing/message to `{agent.name}`.",
            f"Attach {len(attachment_paths)} file(s)." if attachment_paths else "Send without file attachments.",
            "Queue the Gobii to process the message." if trigger_processing else "Store the message without triggering processing.",
        ],
    )

    sender_address = build_web_user_address(user_id=invoking_agent.user_id, agent_id=agent.id)
    if not agent.is_sender_whitelisted(CommsChannel.WEB, sender_address):
        raise MetaGobiiToolError("Invoking Gobii's owner is not allowed to message this agent.")

    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        raise MetaGobiiToolError(str(exc)) from exc

    with transaction.atomic():
        message, conversation = inject_internal_web_message(
            agent.id,
            body,
            sender_user_id=invoking_agent.user_id,
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
        "message_id": str(message.id),
        "agent_id": str(agent.id),
        "cursor": cursor,
        "latest_cursor": cursor,
        "created_at": _iso(message.timestamp),
        "message": _serialize_message(message),
        "timeline_event": event,
        "conversation_id": str(conversation.id),
        "attachment_count": len(resolved_attachments),
    }


def _tool_get_agent_timeline(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    after_cursor = params.get("after_cursor")
    direction = "newer" if after_cursor else params.get("direction") or "initial"
    if direction not in {"initial", "older", "newer"}:
        raise MetaGobiiToolError("direction must be one of initial, older, or newer.")
    limit = _bounded_int(params.get("limit", TIMELINE_DEFAULT_PAGE_SIZE), "limit", minimum=1, maximum=TIMELINE_MAX_PAGE_SIZE)
    cursor = after_cursor or params.get("cursor") or None
    if cursor is not None and not isinstance(cursor, str):
        raise MetaGobiiToolError("cursor/after_cursor must be a string.")
    _validate_timeline_cursor(cursor, "cursor/after_cursor")

    window = fetch_timeline_window(agent, cursor=cursor, direction=direction, limit=limit)
    return {
        "status": "ok",
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


def _tool_wait_for_agent_event(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    after_cursor = params.get("after_cursor") or None
    if after_cursor is not None and not isinstance(after_cursor, str):
        raise MetaGobiiToolError("after_cursor must be a string.")
    _validate_timeline_cursor(after_cursor, "after_cursor")

    timeout_seconds = _bounded_int(
        params.get("timeout_seconds", WAIT_DEFAULT_TIMEOUT_SECONDS),
        "timeout_seconds",
        minimum=0,
        maximum=WAIT_MAX_TIMEOUT_SECONDS,
    )
    limit = _bounded_int(params.get("limit", 20), "limit", minimum=1, maximum=TIMELINE_MAX_PAGE_SIZE)
    event_types = _normalize_wait_event_types(params.get("event_types"))
    filters = _normalize_wait_filters(params.get("filters"))

    start = time.monotonic()
    deadline = start + timeout_seconds
    latest_cursor = after_cursor

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
            return {
                "status": "ok",
                "matched": True,
                "timed_out": False,
                "events": events,
                "next_cursor": latest_cursor,
                "latest_cursor": latest_cursor,
                "waited_seconds": round(time.monotonic() - start, 3),
            }
        if time.monotonic() >= deadline:
            return {
                "status": "ok",
                "matched": False,
                "timed_out": True,
                "events": [],
                "next_cursor": latest_cursor,
                "latest_cursor": latest_cursor,
                "waited_seconds": round(time.monotonic() - start, 3),
            }
        sleep_seconds = min(WAIT_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic()))
        if sleep_seconds:
            time.sleep(sleep_seconds)


def _tool_list_agent_files(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    filespace = get_or_create_default_filespace(agent)
    nodes = (
        AgentFsNode.objects.alive()
        .filter(filespace=filespace)
        .only("id", "parent_id", "name", "path", "node_type", "size_bytes", "mime_type", "created_at", "updated_at")
        .order_by("parent_id", "node_type", "name")
    )
    return {
        "status": "ok",
        "filespace": {"id": str(filespace.id), "name": filespace.name},
        "nodes": [_serialize_file_node(node) for node in nodes],
    }


def _tool_upload_agent_file(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    path = _required_string(params, "path", allow_blank=False)
    content_base64 = _required_string(params, "content_base64", allow_blank=False)
    mime_type = params.get("mime_type") or "application/octet-stream"
    if not isinstance(mime_type, str):
        raise MetaGobiiToolError("mime_type must be a string.")
    overwrite = _optional_bool(params.get("overwrite", False), "overwrite")
    _require_user_confirmed(
        params,
        action=f"upload a file to `{agent.name}`",
        proposed_actions=[
            f"Write `{path}` into `{agent.name}` filespace.",
            "Overwrite an existing file at that path." if overwrite else "Do not overwrite an existing file.",
        ],
    )
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MetaGobiiToolError("content_base64 must be valid base64.") from exc
    if len(content) > MAX_UPLOAD_BYTES:
        raise MetaGobiiToolError("File upload exceeds the 5 MiB direct tool limit.")
    result = write_bytes_to_dir(agent, content, path=path, mime_type=mime_type, overwrite=overwrite)
    if result.get("status") != "ok":
        raise MetaGobiiToolError(result.get("message") or "File upload failed.", result)
    return result


def _tool_list_contacts(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    include_inactive = _optional_bool(params.get("include_inactive", False), "include_inactive")
    contacts = CommsAllowlistEntry.objects.filter(agent=agent).order_by("channel", "address")
    if not include_inactive:
        contacts = contacts.filter(is_active=True)
    return {"status": "ok", "contacts": [_serialize_contact(contact) for contact in contacts]}


def _tool_add_contact(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    channel = _parse_channel(params.get("channel"), allowed={CommsChannel.EMAIL, CommsChannel.SMS, CommsChannel.WEB})
    address = _normalize_address(channel, _required_string(params, "address", allow_blank=False))
    allow_inbound = _optional_bool(params.get("allow_inbound", True), "allow_inbound")
    allow_outbound = _optional_bool(params.get("allow_outbound", True), "allow_outbound")
    can_configure = _optional_bool(params.get("can_configure", False), "can_configure")
    _require_user_confirmed(
        params,
        action=f"add a {channel.value} contact for `{agent.name}`",
        proposed_actions=[
            f"Add or reactivate one {channel.value} contact for `{agent.name}`.",
            f"Set inbound={allow_inbound}, outbound={allow_outbound}, can_configure={can_configure}.",
            "Switch the Gobii to manual allowlist policy if needed.",
        ],
    )

    contact = CommsAllowlistEntry.objects.filter(agent=agent, channel=channel.value, address__iexact=address).first()
    created = False
    if contact is None:
        contact = CommsAllowlistEntry(
            agent=agent,
            channel=channel.value,
            address=address,
        )
        created = True
    contact.is_active = True
    contact.allow_inbound = allow_inbound
    contact.allow_outbound = allow_outbound
    contact.can_configure = can_configure
    contact.save()
    if agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
        agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
        agent.save(update_fields=["whitelist_policy"])
    return {"status": "ok", "contact": _serialize_contact(contact), "created": created}


def _tool_remove_contact(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    contact_id = _parse_uuid(params.get("contact_id"), "contact_id")
    contact = CommsAllowlistEntry.objects.filter(agent=agent, id=contact_id).first()
    if contact is None:
        raise MetaGobiiToolError("Contact not found or inaccessible.")
    _require_user_confirmed(
        params,
        action=f"remove a {contact.channel} contact from `{agent.name}`",
        proposed_actions=[f"Deactivate one {contact.channel} allowlist contact for `{agent.name}`."],
    )
    if contact.is_active:
        contact.is_active = False
        contact.save(update_fields=["is_active", "updated_at"])
        message = "Contact deactivated. Add the same contact again to reactivate it."
    else:
        message = "Contact was already inactive."
    return {"status": "ok", "message": message, "contact": _serialize_contact(contact)}


def _tool_list_pending_contacts(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    requests = CommsAllowlistRequest.objects.filter(
        agent=agent,
        status=CommsAllowlistRequest.RequestStatus.PENDING,
    ).order_by("-requested_at")
    return {"status": "ok", "requests": [_serialize_contact_request(request_obj) for request_obj in requests]}


def _tool_approve_pending_contact(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    request_id = _parse_uuid(params.get("request_id"), "request_id")
    decision = params.get("decision") or "approve"
    if decision not in {"approve", "reject"}:
        raise MetaGobiiToolError("decision must be approve or reject.")
    request_preview = CommsAllowlistRequest.objects.filter(agent=agent, id=request_id).first()
    if request_preview is None:
        raise MetaGobiiToolError("Pending contact request not found or inaccessible.")
    if request_preview.status != CommsAllowlistRequest.RequestStatus.PENDING:
        raise MetaGobiiToolError("This contact request is no longer pending.")
    if request_preview.is_expired():
        raise MetaGobiiToolError("This contact request has expired.")
    _require_user_confirmed(
        params,
        action=f"{decision} a pending contact request for `{agent.name}`",
        proposed_actions=[
            f"{decision.capitalize()} one pending {request_preview.channel} contact request for `{agent.name}`.",
            (
                "If approved, set "
                f"inbound={_optional_bool(params.get('allow_inbound', True), 'allow_inbound')}, "
                f"outbound={_optional_bool(params.get('allow_outbound', True), 'allow_outbound')}, "
                f"can_configure={_optional_bool(params.get('can_configure', False), 'can_configure')}."
            )
            if decision == "approve"
            else "Reject the request without creating an allowlist contact.",
        ],
    )
    contact = None
    with transaction.atomic():
        request_obj = CommsAllowlistRequest.objects.select_for_update().filter(agent=agent, id=request_id).first()
        if request_obj is None:
            raise MetaGobiiToolError("Pending contact request not found or inaccessible.")
        if request_obj.status != CommsAllowlistRequest.RequestStatus.PENDING:
            raise MetaGobiiToolError("This contact request is no longer pending.")
        if request_obj.is_expired():
            raise MetaGobiiToolError("This contact request has expired.")

        if decision == "approve":
            request_obj.request_inbound = _optional_bool(params.get("allow_inbound", True), "allow_inbound")
            request_obj.request_outbound = _optional_bool(params.get("allow_outbound", True), "allow_outbound")
            request_obj.request_configure = _optional_bool(params.get("can_configure", False), "can_configure")
            request_obj.save(update_fields=["request_inbound", "request_outbound", "request_configure"])
            contact = request_obj.approve(invited_by=invoking_agent.user, skip_invitation=True)
            status = "approved"
        else:
            request_obj.reject()
            status = "rejected"
    request_obj.refresh_from_db()
    result = {"status": status, "request": _serialize_contact_request(request_obj)}
    if isinstance(contact, CommsAllowlistEntry):
        result["contact"] = _serialize_contact(contact)
    return result


def _tool_list_contact_endpoints(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    endpoint_ids = set(agent.comms_endpoints.values_list("id", flat=True))
    if agent.preferred_contact_endpoint_id:
        endpoint_ids.add(agent.preferred_contact_endpoint_id)
    endpoints = PersistentAgentCommsEndpoint.objects.filter(id__in=endpoint_ids).order_by("channel", "address")
    return {"status": "ok", "endpoints": [_serialize_endpoint(agent, endpoint) for endpoint in endpoints]}


def _tool_set_preferred_contact_endpoint(invoking_agent: PersistentAgent, params: dict[str, Any]) -> dict[str, Any]:
    agent = _get_agent(invoking_agent, params.get("agent_id"))
    clear = _optional_bool(params.get("clear", False), "clear")
    endpoint = None
    if clear:
        _require_user_confirmed(
            params,
            action=f"clear `{agent.name}` preferred contact endpoint",
            proposed_actions=[f"Clear the preferred contact endpoint for `{agent.name}`."],
        )
        agent.preferred_contact_endpoint = None
        agent.save(update_fields=["preferred_contact_endpoint"])
        return {"status": "ok", "agent": _serialize_agent(agent), "preferred_contact_endpoint": None}

    endpoint_id = params.get("endpoint_id")
    channel = params.get("channel")
    if endpoint_id and channel:
        raise MetaGobiiToolError("Pass either endpoint_id or channel, not both.")
    if endpoint_id:
        endpoint_uuid = _parse_uuid(endpoint_id, "endpoint_id")
        endpoint = PersistentAgentCommsEndpoint.objects.filter(id=endpoint_uuid).first()
        if endpoint is None or not _endpoint_safe_for_preferred_contact(agent, endpoint):
            raise MetaGobiiToolError("Contact endpoint not found, inaccessible, or unsafe for this agent.")
    elif channel:
        endpoint = _resolve_preferred_endpoint_channel(agent, channel)
    else:
        raise MetaGobiiToolError("Pass endpoint_id, channel, or clear=true.")

    _require_user_confirmed(
        params,
        action=f"set `{agent.name}` preferred contact endpoint",
        proposed_actions=[f"Set the preferred contact endpoint for `{agent.name}` to a safe {endpoint.channel} endpoint."],
    )
    agent.preferred_contact_endpoint = endpoint
    agent.save(update_fields=["preferred_contact_endpoint"])
    return {
        "status": "ok",
        "agent": _serialize_agent(agent),
        "preferred_contact_endpoint": _serialize_endpoint(agent, endpoint),
    }


def _resolve_peer_link(invoking_agent: PersistentAgent, params: dict[str, Any]) -> AgentPeerLink:
    accessible = _agent_queryset(invoking_agent).only("id")
    peer_link_id = params.get("peer_link_id")
    if peer_link_id:
        link_id = _parse_uuid(peer_link_id, "peer_link_id")
        link = (
            AgentPeerLink.objects.filter(id=link_id)
            .filter(Q(agent_a__in=accessible) | Q(agent_b__in=accessible))
            .select_related("agent_a", "agent_b", "created_by")
            .first()
        )
    else:
        agent = _get_agent(invoking_agent, params.get("agent_id"))
        peer_agent = _get_agent(invoking_agent, params.get("peer_agent_id"))
        pair_key = AgentPeerLink.build_pair_key(agent.id, peer_agent.id)
        link = (
            AgentPeerLink.objects.filter(pair_key=pair_key)
            .select_related("agent_a", "agent_b", "created_by")
            .first()
        )
    if link is None:
        raise MetaGobiiToolError("Peer link not found or inaccessible.")
    return link


def _resolve_requested_tier_or_error(owner: Any, requested: Any):
    if not isinstance(requested, str) or not requested.strip():
        raise MetaGobiiToolError(
            "preferred_llm_tier must be a supported intelligence tier key.",
            {"field": "preferred_llm_tier"},
        )
    requested_key = requested.strip().lower()
    if requested_key not in {tier.value for tier in AgentLLMTier}:
        raise MetaGobiiToolError(
            "preferred_llm_tier is not a known intelligence tier key.",
            {"field": "preferred_llm_tier", "requested": requested_key},
        )
    try:
        resolved = resolve_preferred_tier_for_owner(owner, requested_key)
        tier = resolve_intelligence_tier_for_owner(owner, requested_key)
    except ValueError as exc:
        raise MetaGobiiToolError(
            "preferred_llm_tier is not supported for this owner scope.",
            {"field": "preferred_llm_tier", "requested": requested_key},
        ) from exc
    if resolved.value != requested_key:
        raise MetaGobiiToolError(
            "preferred_llm_tier exceeds the owner plan or quota limit.",
            {
                "field": "preferred_llm_tier",
                "requested": requested_key,
                "max_allowed": resolved.value,
            },
        )
    return tier


def _normalize_daily_credit_limit(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise MetaGobiiToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
    if isinstance(value, int):
        limit = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise MetaGobiiToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
        limit = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped.isdecimal():
            raise MetaGobiiToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
        limit = int(stripped)
    else:
        raise MetaGobiiToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
    if limit < 1:
        raise MetaGobiiToolError("daily_credit_limit must be a positive integer or null.", {"field": "daily_credit_limit"})
    return limit


def _queue_agent_settings_resume_if_needed(
    agent: PersistentAgent,
    *,
    previous_daily_credit_limit: int | None,
    previous_tier_id,
    previous_tier_key: str,
) -> None:
    daily_limit_changed = agent.daily_credit_limit != previous_daily_credit_limit
    preferred_tier_changed = agent.preferred_llm_tier_id != previous_tier_id
    if not daily_limit_changed and not preferred_tier_changed:
        return
    from api.services.agent_settings_resume import queue_settings_change_resume

    queue_settings_change_resume(
        agent,
        daily_credit_limit_changed=daily_limit_changed,
        previous_daily_credit_limit=previous_daily_credit_limit,
        preferred_llm_tier_changed=preferred_tier_changed,
        previous_preferred_llm_tier_key=previous_tier_key,
        source="meta_gobii_update_agent",
    )


def _schedule_charter_artifacts_on_commit(agent: PersistentAgent) -> None:
    agent_id = agent.id

    def _schedule() -> None:
        fresh_agent = PersistentAgent.objects.filter(id=agent_id).first()
        if fresh_agent is None:
            return
        from api.agent.avatar import maybe_schedule_agent_avatar
        from api.agent.short_description import maybe_schedule_mini_description, maybe_schedule_short_description
        from api.agent.tags import maybe_schedule_agent_tags

        maybe_schedule_short_description(fresh_agent)
        maybe_schedule_mini_description(fresh_agent)
        maybe_schedule_agent_tags(fresh_agent)
        maybe_schedule_agent_avatar(fresh_agent)

    transaction.on_commit(_schedule)


def _build_intelligence_options(owner: Any) -> dict[str, Any]:
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
                "credit_multiplier": _json_safe_scalar(tier.credit_multiplier),
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


def _build_daily_credit_options(owner: Any, tier: Any) -> dict[str, Any]:
    credit_settings = get_daily_credit_settings_for_owner(owner)
    multiplier = get_tier_credit_multiplier(tier)
    slider_bounds = calculate_daily_credit_slider_bounds(credit_settings, tier_multiplier=multiplier)
    return {
        "type": "integer_or_null",
        "null_behavior": "unlimited",
        "soft_target_description": "Preferred daily credit target before agents are asked to slow down.",
        "hard_limit_description": "Gobii enforces a hard stop at soft target multiplied by hard_limit_multiplier.",
        "hard_limit_multiplier": _json_safe_scalar(credit_settings.hard_limit_multiplier),
        "default_daily_credit_target": _json_safe_scalar(credit_settings.default_daily_credit_target),
        "recommended_min": _json_safe_scalar(slider_bounds["slider_min"]),
        "recommended_max": _json_safe_scalar(slider_bounds["slider_limit_max"]),
        "step": _json_safe_scalar(slider_bounds["slider_step"]),
        "tier_credit_multiplier": _json_safe_scalar(multiplier),
        "enforced_by_agent_runtime": True,
    }


def _resolve_preferred_endpoint_channel(agent: PersistentAgent, channel_key: str) -> PersistentAgentCommsEndpoint:
    try:
        channel = CommsChannel(channel_key)
    except ValueError as exc:
        raise MetaGobiiToolError("Unsupported contact channel.") from exc
    if channel not in {CommsChannel.EMAIL, CommsChannel.SMS}:
        raise MetaGobiiToolError("Preferred contact endpoint channel must be email or sms.")

    endpoint = (
        PersistentAgentCommsEndpoint.objects.filter(owner_agent=agent, channel=channel.value)
        .order_by("-is_primary", "address")
        .first()
    )
    if endpoint:
        return endpoint

    if agent.organization_id:
        raise MetaGobiiToolError(f"Organization-owned agent has no {channel.value} endpoint available.")

    if channel == CommsChannel.EMAIL:
        email = (agent.user.email or "").strip().lower()
        if not email:
            raise MetaGobiiToolError("Owner user email is required to select email as preferred contact endpoint.")
        endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.EMAIL,
            address=email,
            defaults={"owner_agent": None},
        )
        return endpoint

    phone_number = UserPhoneNumber.objects.filter(user=agent.user, is_verified=True).order_by("-is_primary", "-created_at").first()
    if phone_number is None:
        raise MetaGobiiToolError("Owner user has no verified primary SMS number.")
    endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.SMS,
        address=phone_number.phone_number,
        defaults={"owner_agent": None},
    )
    return endpoint


def _endpoint_safe_for_preferred_contact(agent: PersistentAgent, endpoint: PersistentAgentCommsEndpoint) -> bool:
    if endpoint.owner_agent_id == agent.id:
        return True
    if endpoint.owner_agent_id is not None:
        return False
    if endpoint.id == agent.preferred_contact_endpoint_id:
        return True
    if endpoint.channel == CommsChannel.EMAIL:
        return endpoint.address.lower() == (agent.user.email or "").strip().lower()
    if endpoint.channel == CommsChannel.SMS:
        return UserPhoneNumber.objects.filter(
            user=agent.user,
            phone_number__iexact=endpoint.address,
            is_verified=True,
        ).exists()
    return False


def _serialize_owner_ref(owner: Any) -> dict[str, Any] | None:
    if owner is None:
        return None
    owner_meta = getattr(owner, "_meta", None)
    model_name = getattr(owner_meta, "model_name", "")
    if model_name == "organization":
        return {"type": "organization", "id": str(owner.id), "name": getattr(owner, "name", "")}
    return {"type": "user", "id": owner.id}


def _serialize_agent(agent: PersistentAgent) -> dict[str, Any]:
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
        "preferred_contact_endpoint_id": str(agent.preferred_contact_endpoint_id) if agent.preferred_contact_endpoint_id else None,
        "preferred_llm_tier": getattr(getattr(agent, "preferred_llm_tier", None), "key", None),
        "daily_credit_limit": agent.daily_credit_limit,
        "daily_credit_soft_target": _json_safe_scalar(agent.get_daily_credit_soft_target()),
        "daily_credit_hard_limit": _json_safe_scalar(agent.get_daily_credit_hard_limit()),
        "proactive_opt_in": agent.proactive_opt_in,
        "proactive_last_trigger_at": _iso(agent.proactive_last_trigger_at),
        "is_deleted": agent.is_deleted,
        "deleted_at": _iso(agent.deleted_at),
    }


def _serialize_agent_ref(agent: PersistentAgent) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "name": agent.name,
        "is_active": agent.is_active,
        "life_state": agent.life_state,
        "is_deleted": agent.is_deleted,
    }


def _serialize_peer_link(link: AgentPeerLink) -> dict[str, Any]:
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


def _serialize_message(message: Any) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "owner_agent_id": str(message.owner_agent_id) if message.owner_agent_id else None,
        "conversation_id": str(message.conversation_id) if message.conversation_id else None,
        "is_outbound": message.is_outbound,
        "body": message.body,
        "timestamp": _iso(message.timestamp),
    }


def _serialize_file_node(node: AgentFsNode) -> dict[str, Any]:
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


def _serialize_contact(contact: CommsAllowlistEntry) -> dict[str, Any]:
    return {
        "id": str(contact.id),
        "agent_id": str(contact.agent_id),
        "channel": contact.channel,
        "address": contact.address,
        "is_active": contact.is_active,
        "allow_inbound": contact.allow_inbound,
        "allow_outbound": contact.allow_outbound,
        "can_configure": contact.can_configure,
        "created_at": _iso(contact.created_at),
        "updated_at": _iso(contact.updated_at),
    }


def _serialize_contact_request(request_obj: CommsAllowlistRequest) -> dict[str, Any]:
    return {
        "id": str(request_obj.id),
        "agent_id": str(request_obj.agent_id),
        "channel": request_obj.channel,
        "address": request_obj.address,
        "name": request_obj.name or None,
        "reason": request_obj.reason,
        "purpose": request_obj.purpose,
        "request_inbound": request_obj.request_inbound,
        "request_outbound": request_obj.request_outbound,
        "request_configure": request_obj.request_configure,
        "status": request_obj.status,
        "requested_at": _iso(request_obj.requested_at),
        "responded_at": _iso(request_obj.responded_at),
        "expires_at": _iso(request_obj.expires_at),
    }


def _serialize_endpoint(agent: PersistentAgent, endpoint: PersistentAgentCommsEndpoint) -> dict[str, Any]:
    return {
        "id": str(endpoint.id),
        "owner_agent_id": str(endpoint.owner_agent_id) if endpoint.owner_agent_id else None,
        "channel": endpoint.channel,
        "address": endpoint.address,
        "is_primary": endpoint.is_primary,
        "is_preferred": endpoint.id == agent.preferred_contact_endpoint_id,
        "safe_for_preferred_contact": _endpoint_safe_for_preferred_contact(agent, endpoint),
    }


def _validate_timeline_cursor(cursor: str | None, key: str) -> None:
    if not cursor:
        return
    parts = cursor.split(":", 2)
    if len(parts) != 3:
        raise MetaGobiiToolError(f"{key} must be a valid Gobii timeline cursor.")
    value, kind, identifier = parts
    try:
        int(value)
    except ValueError as exc:
        raise MetaGobiiToolError(f"{key} must be a valid Gobii timeline cursor.") from exc
    if kind not in {"message", "step", "thinking", "kanban", "plan"} or not identifier:
        raise MetaGobiiToolError(f"{key} must be a valid Gobii timeline cursor.")


def _normalize_wait_event_types(value: Any) -> set[str] | None:
    if value in (None, []):
        return None
    if not isinstance(value, list):
        raise MetaGobiiToolError("event_types must be an array.", {"field": "event_types"})
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
        raise MetaGobiiToolError(
            "event_types contains unsupported values.",
            {"field": "event_types", "unsupported_values": invalid, "supported_values": sorted(WAIT_EVENT_TYPES)},
        )
    return normalized


def _normalize_wait_filters(value: Any) -> dict[str, str]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise MetaGobiiToolError("filters must be an object.", {"field": "filters"})
    unsupported = sorted(set(value) - WAIT_FILTER_FIELDS)
    if unsupported:
        raise MetaGobiiToolError(
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
                raise MetaGobiiToolError(f"filters.{key} must be a non-empty string.")
            filters[key] = raw.strip()
    if "from_actor_type" in filters and filters["from_actor_type"] not in {"agent", "human_user", "external", "system"}:
        raise MetaGobiiToolError(
            "filters.from_actor_type contains an unsupported value.",
            {"field": "from_actor_type", "supported_values": ["agent", "human_user", "external", "system"]},
        )
    return filters


def _wait_event_matches(agent: PersistentAgent, event: dict[str, Any], *, event_types: set[str] | None, filters: dict[str, str]) -> bool:
    kind = event.get("kind")
    if event_types is not None and kind not in event_types:
        return False
    if not filters:
        return True
    for key, expected in filters.items():
        if kind == "steps" and key in {"status", "tool_name"}:
            if not _steps_event_has_value(event, key, expected):
                return False
            continue
        if _wait_event_field_value(agent, event, key) != expected:
            return False
    return True


def _steps_event_has_value(event: dict[str, Any], key: str, expected: str) -> bool:
    entry_key = "status" if key == "status" else "toolName"
    return any((entry.get(entry_key) or "") == expected for entry in event.get("entries") or [])


def _wait_event_field_value(agent: PersistentAgent, event: dict[str, Any], key: str) -> str | None:
    kind = event.get("kind")
    if kind == "message":
        message = event.get("message") or {}
        peer_agent = message.get("peerAgent") or {}
        is_peer = bool(message.get("isPeer"))
        is_outbound = bool(message.get("isOutbound"))
        if key == "from_actor_type":
            if is_peer or is_outbound:
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


def _parse_channel(value: Any, *, allowed: set[CommsChannel]) -> CommsChannel:
    try:
        channel = CommsChannel(str(value).strip().lower())
    except ValueError as exc:
        raise MetaGobiiToolError("Unsupported contact channel.") from exc
    if channel not in allowed:
        raise MetaGobiiToolError("Unsupported contact channel.")
    return channel


def _normalize_address(channel: CommsChannel, address: str) -> str:
    normalized = PersistentAgentCommsEndpoint.normalize_address(channel.value, address) or ""
    if not normalized:
        raise MetaGobiiToolError("address cannot be blank.")
    return normalized


def _required_string(params: dict[str, Any], key: str, *, allow_blank: bool) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise MetaGobiiToolError(f"{key} must be a string.")
    if not allow_blank and not value.strip():
        raise MetaGobiiToolError(f"{key} cannot be blank.")
    return value if allow_blank else value.strip()


def _optional_string(value: Any, key: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise MetaGobiiToolError(f"{key} must be a string.")
    return value.strip()


def _optional_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise MetaGobiiToolError(f"{key} must be a boolean.")


def _optional_choice(value: Any, key: str, choices: set[str], *, allow_missing: bool) -> str | None:
    if value in (None, ""):
        if allow_missing:
            return None
        raise MetaGobiiToolError(f"{key} is required.")
    if not isinstance(value, str):
        raise MetaGobiiToolError(f"{key} must be a string.")
    normalized = value.strip()
    if normalized not in choices:
        raise MetaGobiiToolError(f"{key} must be one of: {', '.join(sorted(choices))}.")
    return normalized


def _bounded_int(value: Any, key: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise MetaGobiiToolError(f"{key} must be an integer.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MetaGobiiToolError(f"{key} must be an integer.") from exc
    if number < minimum or number > maximum:
        raise MetaGobiiToolError(f"{key} must be between {minimum} and {maximum}.")
    return number


def _parse_uuid(value: Any, key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise MetaGobiiToolError(f"{key} must be a valid UUID.") from exc


def _format_validation_error(exc: Exception) -> Any:
    if hasattr(exc, "message_dict"):
        return _json_safe(getattr(exc, "message_dict"))
    if hasattr(exc, "messages"):
        return _json_safe(getattr(exc, "messages"))
    if exc.args:
        return _json_safe(exc.args[0])
    return {"message": str(exc)}


def _json_safe_scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


_HANDLERS: dict[str, Callable[[PersistentAgent, dict[str, Any]], dict[str, Any]]] = {
    "meta_gobii_list_agents": _tool_list_agents,
    "meta_gobii_get_agent": _tool_get_agent,
    "meta_gobii_create_agent": _tool_create_agent,
    "meta_gobii_request_agent_creation": _tool_request_agent_creation,
    "meta_gobii_update_agent": _tool_update_agent,
    "meta_gobii_archive_agent": _tool_archive_agent,
    "meta_gobii_get_agent_config_options": _tool_get_agent_config_options,
    "meta_gobii_list_agent_links": _tool_list_agent_links,
    "meta_gobii_link_agents": _tool_link_agents,
    "meta_gobii_unlink_agents": _tool_unlink_agents,
    "meta_gobii_send_agent_message": _tool_send_agent_message,
    "meta_gobii_get_agent_timeline": _tool_get_agent_timeline,
    "meta_gobii_wait_for_agent_event": _tool_wait_for_agent_event,
    "meta_gobii_list_agent_files": _tool_list_agent_files,
    "meta_gobii_upload_agent_file": _tool_upload_agent_file,
    "meta_gobii_list_contacts": _tool_list_contacts,
    "meta_gobii_add_contact": _tool_add_contact,
    "meta_gobii_remove_contact": _tool_remove_contact,
    "meta_gobii_list_pending_contacts": _tool_list_pending_contacts,
    "meta_gobii_approve_pending_contact": _tool_approve_pending_contact,
    "meta_gobii_list_contact_endpoints": _tool_list_contact_endpoints,
    "meta_gobii_set_preferred_contact_endpoint": _tool_set_preferred_contact_endpoint,
}
