from django.db import transaction

from api.models import PersistentAgent, PersistentAgentMessage
from util.subscription_helper import reconcile_user_plan_from_stripe
from util.trial_enforcement import can_user_use_personal_agents_and_api


ACTIVE_SIGNUP_PREVIEW_STATES = frozenset(
    {
        PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE,
        PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION,
    }
)


def get_signup_preview_creation_state(preview_creation_allowed: bool) -> str | None:
    if not preview_creation_allowed:
        return None
    return PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE


def is_signup_preview_state_active(agent: PersistentAgent | None) -> bool:
    if agent is None:
        return False
    return getattr(agent, "signup_preview_state", None) in ACTIVE_SIGNUP_PREVIEW_STATES


def user_can_access_signup_preview_agent(agent: PersistentAgent | None, user) -> bool:
    if agent is None:
        return False
    if getattr(agent, "organization_id", None) is not None:
        return False
    if getattr(agent, "user_id", None) != getattr(user, "id", None):
        return False
    return is_signup_preview_state_active(agent)


def can_bypass_email_verification_for_signup_preview_first_email(
    agent: PersistentAgent | None,
) -> bool:
    return is_signup_preview_first_reply_window(agent)


def is_signup_preview_first_reply_window(agent: PersistentAgent | None) -> bool:
    if agent is None:
        return False
    if getattr(agent, "signup_preview_state", None) != PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE:
        return False
    return not PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        is_outbound=True,
    ).exists()


def can_bypass_task_credit_for_signup_preview(agent: PersistentAgent | None) -> bool:
    return is_signup_preview_first_reply_window(agent)


def has_followup_user_message_after_signup_preview_reply(
    agent: PersistentAgent | None,
) -> bool:
    if agent is None:
        return False

    first_outbound_at = (
        PersistentAgentMessage.objects
        .filter(owner_agent=agent, is_outbound=True)
        .order_by("timestamp")
        .values_list("timestamp", flat=True)
        .first()
    )
    if first_outbound_at is None:
        return False

    return PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        is_outbound=False,
        timestamp__gt=first_outbound_at,
    ).exists()


def clear_personal_agent_access_cache(user) -> None:
    for attr in (
        "_personal_agents_and_api_access_allowed",
        "_personal_agent_chat_access_allowed",
    ):
        if hasattr(user, attr):
            delattr(user, attr)


def _enqueue_signup_preview_resume_processing(agent_ids: list[str]) -> None:
    if not agent_ids:
        return

    def _enqueue() -> None:
        from api.agent.tasks import process_agent_events_task

        for agent_id in agent_ids:
            process_agent_events_task.delay(agent_id)

    transaction.on_commit(_enqueue)


def _resume_signup_preview_agents_if_user_eligible(
    agent_queryset,
    user,
    *,
    reconcile_plan: bool,
) -> list[str]:
    if getattr(user, "id", None) is None:
        return []

    if reconcile_plan:
        reconcile_user_plan_from_stripe(user)

    clear_personal_agent_access_cache(user)
    if not can_user_use_personal_agents_and_api(user):
        return []

    agents = list(agent_queryset)
    if not agents:
        return []

    resumed_ids = [str(agent.id) for agent in agents]
    requeue_ids = [
        str(agent.id)
        for agent in agents
        if has_followup_user_message_after_signup_preview_reply(agent)
    ]

    updated = PersistentAgent.objects.filter(id__in=resumed_ids).exclude(
        signup_preview_state=PersistentAgent.SignupPreviewState.NONE,
    ).update(
        signup_preview_state=PersistentAgent.SignupPreviewState.NONE,
    )
    if not updated:
        return []

    _enqueue_signup_preview_resume_processing(requeue_ids)
    return resumed_ids


def is_signup_preview_processing_paused(agent: PersistentAgent | None) -> bool:
    if agent is None:
        return False
    return (
        getattr(agent, "signup_preview_state", None)
        == PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
    )


def transition_agent_to_signup_preview_waiting(agent_id) -> bool:
    return bool(
        PersistentAgent.objects.filter(
            id=agent_id,
            signup_preview_state=PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE,
        ).update(
            signup_preview_state=PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION,
        )
    )


def resume_signup_preview_agent_if_eligible(agent: PersistentAgent, user) -> bool:
    if agent.organization_id is not None:
        return False
    if agent.user_id != getattr(user, "id", None):
        return False
    if not is_signup_preview_state_active(agent):
        return False

    resumed_ids = _resume_signup_preview_agents_if_user_eligible(
        PersistentAgent.objects.filter(
            id=agent.id,
            user_id=user.id,
            organization__isnull=True,
            signup_preview_state__in=ACTIVE_SIGNUP_PREVIEW_STATES,
        ),
        user,
        reconcile_plan=True,
    )
    if str(agent.id) not in resumed_ids:
        return False

    agent.signup_preview_state = PersistentAgent.SignupPreviewState.NONE
    return True


def resume_signup_preview_agents_for_user_if_eligible(
    user,
    *,
    reconcile_plan: bool = True,
) -> list[str]:
    if getattr(user, "id", None) is None:
        return []

    return _resume_signup_preview_agents_if_user_eligible(
        PersistentAgent.objects.filter(
            user_id=user.id,
            organization__isnull=True,
            signup_preview_state__in=ACTIVE_SIGNUP_PREVIEW_STATES,
        ),
        user,
        reconcile_plan=reconcile_plan,
    )
