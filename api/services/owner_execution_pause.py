import logging
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from api.models import ExecutionPauseReasonChoices
from api.services.agent_lifecycle import AgentLifecycleService, AgentShutdownReason

logger = logging.getLogger(__name__)


EXECUTION_PAUSE_MESSAGE = "Account execution is paused until billing is resolved."
EXECUTION_PAUSE_NOTE = "owner_execution_paused"

EXECUTION_PAUSE_REASON_BILLING_DELINQUENCY = ExecutionPauseReasonChoices.BILLING_DELINQUENCY
EXECUTION_PAUSE_REASON_TRIAL_CONVERSION_FAILED = ExecutionPauseReasonChoices.TRIAL_CONVERSION_FAILED


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
        }

    return {
        "paused": bool(getattr(billing, "execution_paused", False)),
        "reason": str(getattr(billing, "execution_pause_reason", "") or ""),
        "paused_at": getattr(billing, "execution_paused_at", None),
    }


def is_owner_execution_paused(owner) -> bool:
    return bool(get_owner_execution_pause_state(owner)["paused"])


def pause_owner_execution(
    owner,
    reason: str,
    *,
    source: str = "unknown",
    paused_at=None,
    trigger_agent_cleanup: bool = True,
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
    )

    if state_changed:
        billing.execution_paused = True
        billing.execution_pause_reason = normalized_reason
        billing.execution_paused_at = effective_paused_at
        billing.save(
            update_fields=[
                "execution_paused",
                "execution_pause_reason",
                "execution_paused_at",
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
    billing.save(
        update_fields=[
            "execution_paused",
            "execution_pause_reason",
            "execution_paused_at",
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


def _enqueue_agent_resumes(agent_ids) -> None:
    from api.agent.tasks.process_events import process_agent_events_task

    for agent_id in agent_ids:
        process_agent_events_task.delay(str(agent_id))
