import logging
from datetime import datetime, timezone as dt_timezone
from numbers import Number
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from api.models import ExecutionPauseReasonChoices
from api.services.agent_lifecycle import AgentLifecycleService, AgentShutdownReason
from api.services.billing_pause_notifications import (
    is_billing_execution_pause_reason,
    send_owner_billing_pause_notification,
)
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)


EXECUTION_PAUSE_MESSAGE = "Account execution is paused."
EXECUTION_PAUSE_NOTE = "owner_execution_paused"

EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY = ExecutionPauseReasonChoices.BILLING_DELINQUENCY
EXECUTION_PAUSE_REASON_TRIAL_CONVERSION_FAILED = ExecutionPauseReasonChoices.TRIAL_CONVERSION_FAILED
EXECUTION_PAUSE_REASON_TRIAL_ENDED_NON_RENEWAL = ExecutionPauseReasonChoices.TRIAL_ENDED_NON_RENEWAL
EXECUTION_PAUSE_REASON_ACCOUNT_CANCELLATION = ExecutionPauseReasonChoices.ACCOUNT_CANCELLATION
EXECUTION_PAUSE_REASON_CUSTOMER_ACCOUNT_PAUSE = ExecutionPauseReasonChoices.CUSTOMER_ACCOUNT_PAUSE
SCHEDULED_CUSTOMER_PAUSE_UPDATE_FIELDS = [
    "scheduled_customer_pause_effective_at",
    "scheduled_customer_pause_resume_at",
    "scheduled_customer_pause_subscription_id",
]


def resolve_agent_owner(agent) -> Any:
    organization = getattr(agent, "organization", None)
    if _is_supported_owner_instance(organization):
        return organization

    user = getattr(agent, "user", None)
    if _is_supported_owner_instance(user):
        return user

    return None


def resolve_browser_task_owner(task_obj, *, agent_context=None) -> Any:
    owner = getattr(task_obj, "organization", None)
    if _is_supported_owner_instance(owner):
        return owner

    if agent_context is None:
        browser_agent = getattr(task_obj, "agent", None)
        if browser_agent is not None:
            PersistentAgent = apps.get_model("api", "PersistentAgent")
            try:
                agent_context = browser_agent.persistent_agent
            except PersistentAgent.DoesNotExist:
                agent_context = None

    if agent_context is not None:
        owner = resolve_agent_owner(agent_context)
        if owner is not None:
            return owner

    user = getattr(task_obj, "user", None)
    if _is_supported_owner_instance(user):
        return user

    return None


def resolve_owner_by_ref(owner_type: str, owner_id) -> Any:
    normalized_owner_type = str(owner_type or "").strip().lower()
    if not owner_id:
        return None

    if normalized_owner_type == "user":
        return get_user_model().objects.filter(pk=owner_id).first()

    if normalized_owner_type == "organization":
        Organization = apps.get_model("api", "Organization")
        return Organization.objects.filter(pk=owner_id).first()

    return None


def get_owner_execution_pause_state(owner) -> dict[str, Any]:
    billing = _get_billing_record(owner)
    if billing is None:
        return {
            "paused": False,
            "reason": "",
            "paused_at": None,
            "resume_at": None,
        }

    return {
        "paused": bool(getattr(billing, "execution_paused", False)),
        "reason": str(getattr(billing, "execution_pause_reason", "") or ""),
        "paused_at": getattr(billing, "execution_paused_at", None),
        "resume_at": getattr(billing, "execution_pause_resume_at", None),
    }


def is_owner_execution_paused(owner) -> bool:
    return bool(get_owner_execution_pause_state(owner)["paused"])


def is_customer_account_pause_reason(reason: str) -> bool:
    return str(reason or "").strip() == EXECUTION_PAUSE_REASON_CUSTOMER_ACCOUNT_PAUSE


def is_billing_recovery_resumable_pause_reason(reason: str) -> bool:
    return is_billing_execution_pause_reason(str(reason or "").strip())


def is_owner_customer_account_paused(owner) -> bool:
    state = get_owner_execution_pause_state(owner)
    return bool(state["paused"] and is_customer_account_pause_reason(state["reason"]))


def get_owner_account_pause_state(owner) -> dict[str, Any]:
    state = get_owner_execution_pause_state(owner)
    billing = _get_billing_record(owner)
    scheduled_effective_at = getattr(billing, "scheduled_customer_pause_effective_at", None)
    scheduled_resume_at = getattr(billing, "scheduled_customer_pause_resume_at", None)
    scheduled_subscription_id = str(getattr(billing, "scheduled_customer_pause_subscription_id", "") or "").strip()
    scheduled = bool(scheduled_effective_at and scheduled_resume_at)
    return {
        **state,
        "customer_paused": bool(state["paused"] and is_customer_account_pause_reason(state["reason"])),
        "scheduled": scheduled,
        "scheduled_effective_at": scheduled_effective_at if scheduled else None,
        "scheduled_resume_at": scheduled_resume_at if scheduled else None,
        "scheduled_subscription_id": scheduled_subscription_id if scheduled else "",
    }


def schedule_customer_account_pause(owner, *, effective_at, resume_at, subscription_id: str, source: str = "unknown") -> bool:
    if owner is None or _owner_type_label(owner) != "user":
        return False

    billing = _get_billing_record(owner, create=True)
    if billing is None:
        return False

    normalized_subscription_id = str(subscription_id or "").strip()
    if not effective_at or not resume_at or not normalized_subscription_id:
        return False

    state_changed = (
        billing.scheduled_customer_pause_effective_at != effective_at
        or billing.scheduled_customer_pause_resume_at != resume_at
        or str(billing.scheduled_customer_pause_subscription_id or "").strip() != normalized_subscription_id
    )
    if state_changed:
        billing.scheduled_customer_pause_effective_at = effective_at
        billing.scheduled_customer_pause_resume_at = resume_at
        billing.scheduled_customer_pause_subscription_id = normalized_subscription_id
        billing.save(update_fields=SCHEDULED_CUSTOMER_PAUSE_UPDATE_FIELDS)

    logger.info("Scheduled customer account pause for user %s (subscription=%s effective_at=%s resume_at=%s source=%s changed=%s)", getattr(owner, "id", None), normalized_subscription_id, effective_at, resume_at, source, state_changed)
    return state_changed


def clear_scheduled_customer_account_pause(owner, *, subscription_id: str | None = None, source: str = "unknown") -> bool:
    if owner is None or _owner_type_label(owner) != "user":
        return False

    billing = _get_billing_record(owner)
    if billing is None:
        return False

    normalized_subscription_id = str(subscription_id or "").strip()
    existing_subscription_id = str(billing.scheduled_customer_pause_subscription_id or "").strip()
    if (
        normalized_subscription_id
        and existing_subscription_id
        and existing_subscription_id != normalized_subscription_id
    ):
        return False

    state_changed = _clear_scheduled_customer_pause_for_billing(billing)
    if state_changed:
        logger.info("Cleared scheduled customer account pause for user %s (subscription=%s source=%s)", getattr(owner, "id", None), existing_subscription_id, source)
    return state_changed


def pause_owner_execution(
    owner,
    reason: str,
    *,
    source: str = "unknown",
    paused_at=None,
    resume_at=None,
    trigger_agent_cleanup: bool = True,
    analytics_source: AnalyticsSource = AnalyticsSource.NA,
) -> bool:
    if owner is None:
        return False

    billing = _get_billing_record(owner, create=True)
    if billing is None:
        return False

    normalized_reason = str(reason or "").strip() or "unknown"
    effective_paused_at = paused_at or timezone.now()
    was_paused = bool(getattr(billing, "execution_paused", False))
    state_changed = (
        not was_paused
        or getattr(billing, "execution_pause_reason", "") != normalized_reason
        or getattr(billing, "execution_paused_at", None) is None
        or getattr(billing, "execution_pause_resume_at", None) != resume_at
    )

    if state_changed:
        billing.execution_paused = True
        billing.execution_pause_reason = normalized_reason
        billing.execution_paused_at = effective_paused_at
        billing.execution_pause_resume_at = resume_at
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
                "execution_pause_resume_at",
            ]
        )

    if not was_paused and trigger_agent_cleanup:
        cleanup_meta = {
            "source": source,
            "execution_pause_reason": normalized_reason,
        }
        for agent_id in _iter_active_owner_agent_ids(owner):
            AgentLifecycleService.shutdown(
                str(agent_id),
                AgentShutdownReason.PAUSE,
                meta=cleanup_meta,
            )

    if not was_paused:
        _track_account_execution_paused(
            owner,
            reason=normalized_reason,
            source=source,
            paused_at=effective_paused_at,
            trigger_agent_cleanup=trigger_agent_cleanup,
            analytics_source=analytics_source,
        )
        if is_billing_execution_pause_reason(normalized_reason):
            transaction.on_commit(lambda: send_owner_billing_pause_notification(owner))

    logger.info(
        "Owner execution paused for %s %s (reason=%s source=%s changed=%s)",
        _owner_type_label(owner),
        getattr(owner, "id", None),
        normalized_reason,
        source,
        state_changed,
    )
    return state_changed


def pause_owner_execution_by_ref(
    owner_type: str,
    owner_id,
    reason: str,
    *,
    source: str = "unknown",
    paused_at=None,
    trigger_agent_cleanup: bool = True,
    analytics_source: AnalyticsSource = AnalyticsSource.API,
) -> bool:
    owner = resolve_owner_by_ref(owner_type, owner_id)
    if owner is None:
        logger.warning(
            "Unable to pause execution for missing owner %s/%s",
            owner_type,
            owner_id,
        )
        return False

    return pause_owner_execution(
        owner,
        reason,
        source=source,
        paused_at=paused_at,
        trigger_agent_cleanup=trigger_agent_cleanup,
        analytics_source=analytics_source,
    )


def resume_owner_execution(
    owner,
    *,
    source: str = "unknown",
    enqueue_agent_resume: bool = True,
) -> bool:
    if owner is None:
        return False

    billing = _get_billing_record(owner)
    if billing is None or not getattr(billing, "execution_paused", False):
        return False

    billing.execution_paused = False
    billing.execution_pause_reason = ""
    billing.execution_paused_at = None
    billing.execution_pause_resume_at = None
    billing.save(
        update_fields=[
            "execution_paused",
            "execution_pause_reason",
            "execution_paused_at",
            "execution_pause_resume_at",
        ]
    )

    agent_ids = list(_iter_active_owner_agent_ids(owner)) if enqueue_agent_resume else []
    if agent_ids:
        transaction.on_commit(lambda: _enqueue_agent_resumes(agent_ids))

    logger.info(
        "Owner execution resumed for %s %s (source=%s resumed_agents=%s)",
        _owner_type_label(owner),
        getattr(owner, "id", None),
        source,
        len(agent_ids),
    )
    return True


def resume_owner_execution_by_ref(
    owner_type: str,
    owner_id,
    *,
    source: str = "unknown",
    enqueue_agent_resume: bool = True,
) -> bool:
    owner = resolve_owner_by_ref(owner_type, owner_id)
    if owner is None:
        logger.warning(
            "Unable to resume execution for missing owner %s/%s",
            owner_type,
            owner_id,
        )
        return False

    return resume_owner_execution(
        owner,
        source=source,
        enqueue_agent_resume=enqueue_agent_resume,
    )


def get_customer_account_pause_from_subscription(subscription_payload: Any) -> dict[str, Any]:
    pause_collection = _stripe_object_field(subscription_payload, "pause_collection")
    subscription_id = str(_stripe_object_field(subscription_payload, "id") or "").strip()
    if not pause_collection:
        return {
            "paused": False,
            "effective_at": None,
            "resume_at": None,
            "subscription_id": subscription_id,
        }

    return {
        "paused": True,
        "effective_at": _coerce_datetime(
            _stripe_object_field(subscription_payload, "current_period_end")
        ),
        "resume_at": _coerce_datetime(_stripe_object_field(pause_collection, "resumes_at")),
        "subscription_id": subscription_id,
    }


def sync_owner_customer_account_pause(
    owner,
    *,
    subscription_payload: Any,
    source: str = "unknown",
) -> bool:
    if owner is None:
        return False

    pause_state = get_customer_account_pause_from_subscription(subscription_payload)
    current_state = get_owner_execution_pause_state(owner)
    current_reason = current_state["reason"]

    if pause_state["paused"]:
        effective_at = pause_state.get("effective_at")
        if (
            _owner_type_label(owner) == "user"
            and effective_at is not None
            and effective_at > timezone.now()
        ):
            return schedule_customer_account_pause(
                owner,
                effective_at=effective_at,
                resume_at=pause_state["resume_at"],
                subscription_id=pause_state.get("subscription_id") or "",
                source=source,
            )

        if (
            current_state["paused"]
            and current_reason
            and not (
                is_customer_account_pause_reason(current_reason)
                or is_billing_recovery_resumable_pause_reason(current_reason)
            )
        ):
            return False

        paused_at = (
            current_state["paused_at"]
            if is_customer_account_pause_reason(current_reason) and current_state["paused_at"] is not None
            else None
        )
        clear_scheduled_customer_account_pause(
            owner,
            subscription_id=pause_state.get("subscription_id") or None,
            source=source,
        )
        return pause_owner_execution(
            owner,
            EXECUTION_PAUSE_REASON_CUSTOMER_ACCOUNT_PAUSE,
            source=source,
            paused_at=paused_at,
            resume_at=pause_state["resume_at"],
        )

    scheduled_cleared = clear_scheduled_customer_account_pause(
        owner,
        subscription_id=pause_state.get("subscription_id") or None,
        source=source,
    )
    if is_customer_account_pause_reason(current_reason):
        return bool(resume_owner_execution(owner, source=source) or scheduled_cleared)

    return scheduled_cleared


def apply_customer_account_pause_transitions(*, now=None, limit: int = 500) -> dict[str, int]:
    now = now or timezone.now()
    limit = max(1, int(limit or 500))
    UserBilling = apps.get_model("api", "UserBilling")

    result = {"scheduled_applied": 0, "scheduled_expired": 0, "scheduled_blocked": 0, "active_resumed": 0}
    scheduled_qs = UserBilling.objects.select_related("user").filter(
        scheduled_customer_pause_effective_at__isnull=False,
        scheduled_customer_pause_resume_at__isnull=False,
    )

    expired_qs = (
        scheduled_qs
        .filter(
            scheduled_customer_pause_resume_at__lte=now,
        )
        .order_by("scheduled_customer_pause_resume_at")[:limit]
    )
    for billing in list(expired_qs):
        if _clear_scheduled_customer_pause_for_billing(billing):
            result["scheduled_expired"] += 1

    due_qs = (
        scheduled_qs
        .filter(
            scheduled_customer_pause_effective_at__lte=now,
            scheduled_customer_pause_resume_at__gt=now,
        )
        .order_by("scheduled_customer_pause_effective_at")[:limit]
    )
    for billing in list(due_qs):
        current_reason = str(getattr(billing, "execution_pause_reason", "") or "")
        if (
            getattr(billing, "execution_paused", False)
            and not is_customer_account_pause_reason(current_reason)
        ):
            result["scheduled_blocked"] += 1
            continue

        user = getattr(billing, "user", None)
        if user is None:
            result["scheduled_blocked"] += 1
            continue

        pause_owner_execution(
            user,
            EXECUTION_PAUSE_REASON_CUSTOMER_ACCOUNT_PAUSE,
            source="api.tasks.apply_customer_account_pause_transitions",
            paused_at=billing.scheduled_customer_pause_effective_at,
            resume_at=billing.scheduled_customer_pause_resume_at,
        )
        if _clear_scheduled_customer_pause_for_billing(billing):
            result["scheduled_applied"] += 1

    resume_qs = (
        UserBilling.objects.select_related("user")
        .filter(
            execution_paused=True,
            execution_pause_reason=EXECUTION_PAUSE_REASON_CUSTOMER_ACCOUNT_PAUSE,
            execution_pause_resume_at__isnull=False,
            execution_pause_resume_at__lte=now,
        )
        .order_by("execution_pause_resume_at")[:limit]
    )
    for billing in list(resume_qs):
        user = getattr(billing, "user", None)
        if user is not None and resume_owner_execution(user, source="api.tasks.apply_customer_account_pause_transitions"):
            result["active_resumed"] += 1

    return result


def _owner_type_label(owner) -> str:
    UserModel = get_user_model()
    Organization = apps.get_model("api", "Organization")

    if isinstance(owner, UserModel):
        return "user"
    if isinstance(owner, Organization):
        return "organization"
    return owner.__class__.__name__.lower()


def _is_supported_owner_instance(owner) -> bool:
    if owner is None:
        return False

    UserModel = get_user_model()
    Organization = apps.get_model("api", "Organization")
    return isinstance(owner, (UserModel, Organization))


def _get_billing_record(owner, *, create: bool = False):
    if owner is None:
        return None

    BillingModel, filters, owner_type = _get_billing_model_and_filters(owner)
    if create:
        cached_billing = _get_cached_billing_record(owner)
        if cached_billing is not None:
            try:
                cached_billing.refresh_from_db()
            except cached_billing.__class__.DoesNotExist:
                cached_billing = None
        if cached_billing is not None:
            return cached_billing

        defaults = {}
        if owner_type == "organization":
            defaults["billing_cycle_anchor"] = timezone.now().day
        billing, _created = BillingModel.objects.get_or_create(**filters, defaults=defaults)
        return billing

    return BillingModel.objects.filter(**filters).first()


def _get_cached_billing_record(owner):
    try:
        return owner.billing
    except ObjectDoesNotExist:
        return None
    except AttributeError:
        return None


def _get_billing_model_and_filters(owner):
    owner_type = _owner_type_label(owner)
    if owner_type == "user":
        BillingModel = apps.get_model("api", "UserBilling")
        return BillingModel, {"user": owner}, owner_type
    if owner_type == "organization":
        BillingModel = apps.get_model("api", "OrganizationBilling")
        return BillingModel, {"organization": owner}, owner_type

    raise TypeError(f"Unsupported owner type: {owner.__class__.__name__}")


def _clear_scheduled_customer_pause_for_billing(billing) -> bool:
    if billing is None:
        return False

    state_changed = bool(
        billing.scheduled_customer_pause_effective_at
        or billing.scheduled_customer_pause_resume_at
        or str(billing.scheduled_customer_pause_subscription_id or "").strip()
    )
    if not state_changed:
        return False

    billing.scheduled_customer_pause_effective_at = None
    billing.scheduled_customer_pause_resume_at = None
    billing.scheduled_customer_pause_subscription_id = ""
    billing.save(update_fields=SCHEDULED_CUSTOMER_PAUSE_UPDATE_FIELDS)
    return True


def _iter_active_owner_agent_ids(owner):
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    qs = PersistentAgent.objects.non_eval().alive().filter(
        is_active=True,
        life_state=PersistentAgent.LifeState.ACTIVE,
    )

    if _owner_type_label(owner) == "organization":
        qs = qs.filter(organization_id=owner.id)
    else:
        qs = qs.filter(user_id=owner.id, organization__isnull=True)

    return qs.values_list("id", flat=True).iterator(chunk_size=200)


def _track_account_execution_paused(
    owner,
    *,
    reason: str,
    source: str,
    paused_at,
    trigger_agent_cleanup: bool,
    analytics_source: AnalyticsSource,
) -> None:
    properties = {
        "owner_type": _owner_type_label(owner),
        "owner_id": str(getattr(owner, "id", "") or ""),
        "execution_pause_reason": reason,
        "pause_source": source,
        "trigger_agent_cleanup": bool(trigger_agent_cleanup),
    }
    if paused_at is not None:
        properties["paused_at"] = paused_at.isoformat() if hasattr(paused_at, "isoformat") else str(paused_at)

    analytics_user_id = _analytics_user_id_for_owner(owner)
    if analytics_user_id is not None:
        Analytics.track_event(
            user_id=analytics_user_id,
            event=AnalyticsEvent.ACCOUNT_EXECUTION_PAUSED,
            source=analytics_source,
            properties=properties,
        )
        return

    anonymous_id = _analytics_anonymous_id_for_owner(owner)
    if anonymous_id is not None:
        Analytics.track_event_anonymous(
            anonymous_id=anonymous_id,
            event=AnalyticsEvent.ACCOUNT_EXECUTION_PAUSED,
            source=analytics_source,
            properties=properties,
        )


def _analytics_user_id_for_owner(owner: Any) -> Any | None:
    owner_type = _owner_type_label(owner)
    if owner_type == "user":
        return getattr(owner, "id", None)
    if owner_type == "organization":
        return getattr(owner, "created_by_id", None)
    return None


def _analytics_anonymous_id_for_owner(owner) -> str | None:
    owner_id = getattr(owner, "id", None)
    if owner_id is None:
        return None
    return f"owner:{_owner_type_label(owner)}:{owner_id}"


def _enqueue_agent_resumes(agent_ids) -> None:
    from api.agent.tasks.process_events import process_agent_events_task

    for agent_id in agent_ids:
        process_agent_events_task.delay(str(agent_id))


def _stripe_object_field(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _coerce_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None

    candidate: datetime | None = None

    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, Number):
        try:
            candidate = datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            candidate = None
    elif isinstance(value, str):
        stripped = value.strip()
        parsed = parse_datetime(stripped) if stripped else None
        if parsed is not None:
            candidate = parsed
        else:
            try:
                candidate = datetime.fromtimestamp(float(stripped), tz=dt_timezone.utc)
            except (OverflowError, OSError, ValueError):
                candidate = None

    if candidate is None:
        return None

    if timezone.is_naive(candidate):
        candidate = timezone.make_aware(candidate, timezone=dt_timezone.utc)

    return candidate
