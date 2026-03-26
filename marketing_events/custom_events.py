from enum import StrEnum

from django.contrib.auth import get_user_model
from django.conf import settings

from marketing_events.api import capi_delay_subscription_guarded
from marketing_events.constants import AD_CAPI_PROVIDER_TARGETS
from marketing_events.context import build_marketing_context_from_user
from util.subscription_helper import get_active_subscription, get_owner_plan
from util.user_behavior import (
    count_messages_sent_to_gobii,
    get_custom_capi_event_delay_seconds,
    is_fast_cancel_owner,
    is_owner_currently_in_trial,
)


class ConfiguredCustomEvent(StrEnum):
    AGENT_CREATED = "AgentCreated"
    INBOUND_MESSAGE = "InboundMessage"
    INTEGRATION_ADDED = "IntegrationAdded"
    SECRET_ADDED = "SecretAdded"
    CLONE_GOBII = "CloneGobii"
    TEMPLATE_LAUNCHED = "TemplateLaunched"


INBOUND_MESSAGE_CAPI_COUNTS = frozenset({1, 5, 20})


def _resolve_custom_event_plan_key(plan_owner) -> str | None:
    if plan_owner is None or getattr(plan_owner, "id", None) is None:
        return None

    plan = get_owner_plan(plan_owner) or {}
    plan_id = str(plan.get("id", "") or "").strip().lower()
    if plan_id == "startup":
        return "pro"
    if plan_id in {"scale", "org_team"}:
        return plan_id
    return None


def _is_first_workspace_agent_creation(plan_owner, properties: dict | None = None) -> bool:
    if plan_owner is None or getattr(plan_owner, "id", None) is None:
        return False

    from api.models import Organization, PersistentAgent

    agent_id = str((properties or {}).get("agent_id") or "").strip()
    if not agent_id:
        return False

    if isinstance(plan_owner, Organization):
        first_agent_id = (
            PersistentAgent.objects
            .filter(organization_id=plan_owner.id)
            .order_by("created_at", "id")
            .values_list("id", flat=True)
            .first()
        )
        return str(first_agent_id) == agent_id

    user_model = get_user_model()
    if isinstance(plan_owner, user_model):
        first_agent_id = (
            PersistentAgent.objects
            .filter(
                user_id=plan_owner.id,
                organization_id__isnull=True,
            )
            .order_by("created_at", "id")
            .values_list("id", flat=True)
            .first()
        )
        return str(first_agent_id) == agent_id

    return False


def _resolve_inbound_message_count(user, properties: dict) -> int | None:
    message_count = properties.get("message_count")
    if isinstance(message_count, int):
        return message_count

    message_count = count_messages_sent_to_gobii(user)
    properties["message_count"] = message_count
    return message_count


def _resolve_custom_event_value(
    user,
    event_name: ConfiguredCustomEvent | str,
    *,
    plan_owner=None,
    properties: dict,
) -> float | None:
    plan_key = _resolve_custom_event_plan_key(plan_owner)
    if plan_key is None:
        return None

    plan_values = settings.CAPI_CUSTOM_EVENT_VALUES_BY_PLAN.get(plan_key) or {}
    event_value = plan_values.get(str(event_name))
    if isinstance(event_value, dict):
        message_count = _resolve_inbound_message_count(user, properties)
        if message_count is None:
            return None
        return event_value.get(message_count)

    return event_value


def build_configured_custom_event_properties(
    user,
    event_name: ConfiguredCustomEvent | str,
    *,
    plan_owner=None,
    properties: dict | None = None,
) -> dict:
    event_properties = dict(properties or {})
    if str(event_name) == ConfiguredCustomEvent.INBOUND_MESSAGE:
        message_count = _resolve_inbound_message_count(user, event_properties)
        if message_count is not None:
            event_properties.setdefault("message_count", message_count)

    event_value = _resolve_custom_event_value(
        user,
        event_name,
        plan_owner=plan_owner,
        properties=event_properties,
    )
    if event_value is not None:
        event_properties["value"] = event_value
        event_properties["currency"] = settings.CAPI_CUSTOM_EVENT_CURRENCY

    return event_properties


def _should_enqueue_configured_custom_capi_event(
    user,
    event_name: ConfiguredCustomEvent | str,
    *,
    plan_owner=None,
    properties: dict | None = None,
) -> bool:
    billed_owner = plan_owner or user
    if user is None or getattr(user, "id", None) is None:
        return False
    if not is_owner_currently_in_trial(billed_owner):
        return False
    if is_fast_cancel_owner(billed_owner):
        return False
    if str(event_name) == ConfiguredCustomEvent.INBOUND_MESSAGE:
        event_properties = properties if properties is not None else {}
        message_count = _resolve_inbound_message_count(user, event_properties)
        return message_count in INBOUND_MESSAGE_CAPI_COUNTS
    if str(event_name) == ConfiguredCustomEvent.AGENT_CREATED:
        return _is_first_workspace_agent_creation(billed_owner, properties)
    return True


def emit_configured_custom_capi_event(
    user,
    event_name: ConfiguredCustomEvent | str,
    *,
    plan_owner=None,
    properties: dict | None = None,
    request=None,
    context: dict | None = None,
) -> None:
    resolved_plan_owner = plan_owner or user
    resolved_properties = dict(properties or {})
    if not _should_enqueue_configured_custom_capi_event(
        user,
        event_name,
        plan_owner=resolved_plan_owner,
        properties=resolved_properties,
    ):
        return

    resolved_properties = build_configured_custom_event_properties(
        user,
        event_name,
        plan_owner=resolved_plan_owner,
        properties=resolved_properties,
    )

    resolved_context = context
    if request is None:
        resolved_context = build_marketing_context_from_user(user) | (context or {})

    active_subscription = get_active_subscription(resolved_plan_owner)
    capi_delay_subscription_guarded(
        user=user,
        event_name=str(event_name),
        countdown_seconds=get_custom_capi_event_delay_seconds(resolved_plan_owner),
        subscription_guard_id=getattr(active_subscription, "id", None),
        properties=resolved_properties,
        request=request,
        context=resolved_context,
        provider_targets=AD_CAPI_PROVIDER_TARGETS,
    )
