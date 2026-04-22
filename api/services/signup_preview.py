from dataclasses import dataclass

from django.db import transaction

from api.models import PersistentAgent, PersistentAgentMessage
from util.subscription_helper import reconcile_user_plan_from_stripe
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.trial_enforcement import can_user_use_personal_agents_and_api


ACTIVE_SIGNUP_PREVIEW_STATES = frozenset(
    {
        PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE,
        PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION,
    }
)


@dataclass(frozen=True)
class SignupPreviewResumeResult:
    resumed_agent_ids: tuple[str, ...] = ()
    requeued_agent_ids: tuple[str, ...] = ()

    @property
    def resumed_any(self) -> bool:
        return bool(self.resumed_agent_ids)

    def includes(self, agent: PersistentAgent | None) -> bool:
        if agent is None:
            return False
        return str(agent.id) in self.resumed_agent_ids


def get_signup_preview_creation_state(preview_creation_allowed: bool) -> str | None:
    if not preview_creation_allowed:
        return None
    return PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE


def user_has_existing_personal_agent_for_signup_preview(user) -> bool:
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    return PersistentAgent.objects.filter(
        user_id=user_id,
        organization__isnull=True,
        is_deleted=False,
    ).exists()


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
    return (
        getattr(agent, "signup_preview_state", None)
        == PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE
    )


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


def _track_signup_preview_resumed_agents(
    user,
    agents: list[PersistentAgent],
    requeued_agent_ids: set[str],
    *,
    resume_source: str,
    plan_before: str | None,
    plan_after: str | None,
) -> None:
    user_id = getattr(user, "id", None)
    if user_id is None or not agents:
        return

    tracked_agents = [
        {
            "agent_id": str(agent.id),
            "signup_preview_state": agent.signup_preview_state,
            "has_followup_message": str(agent.id) in requeued_agent_ids,
        }
        for agent in agents
    ]

    def _track() -> None:
        for item in tracked_agents:
            properties = {
                "agent_id": item["agent_id"],
                "signup_preview_state": item["signup_preview_state"],
                "resume_source": resume_source,
                "has_followup_message": item["has_followup_message"],
            }
            if plan_before:
                properties["plan_before"] = plan_before
            if plan_after:
                properties["plan_after"] = plan_after
            Analytics.track_event(
                user_id=user_id,
                event=AnalyticsEvent.SIGNUP_PREVIEW_RESUMED_AFTER_PLAN,
                source=AnalyticsSource.WEB,
                properties=properties,
            )

    transaction.on_commit(_track)


def _resume_signup_preview_agents_if_user_eligible(
    agent_queryset,
    user,
    *,
    reconcile_plan: bool,
    resume_source: str,
    plan_before: str | None,
    plan_after: str | None,
) -> SignupPreviewResumeResult:
    if getattr(user, "id", None) is None:
        return SignupPreviewResumeResult()

    if reconcile_plan:
        reconcile_user_plan_from_stripe(user)

    clear_personal_agent_access_cache(user)
    if not can_user_use_personal_agents_and_api(user):
        return SignupPreviewResumeResult()

    agents = list(agent_queryset)
    if not agents:
        return SignupPreviewResumeResult()

    resumed_ids = [str(agent.id) for agent in agents]
    requeue_ids = [
        str(agent.id)
        for agent in agents
        if has_followup_user_message_after_signup_preview_reply(agent)
    ]
    requeue_id_set = set(requeue_ids)

    updated = PersistentAgent.objects.filter(id__in=resumed_ids).exclude(
        signup_preview_state=PersistentAgent.SignupPreviewState.NONE,
    ).update(
        signup_preview_state=PersistentAgent.SignupPreviewState.NONE,
    )
    if not updated:
        return SignupPreviewResumeResult()

    _enqueue_signup_preview_resume_processing(requeue_ids)
    _track_signup_preview_resumed_agents(
        user,
        agents,
        requeue_id_set,
        resume_source=resume_source,
        plan_before=plan_before,
        plan_after=plan_after,
    )
    return SignupPreviewResumeResult(
        resumed_agent_ids=tuple(resumed_ids),
        requeued_agent_ids=tuple(requeue_ids),
    )


def is_signup_preview_processing_paused(agent: PersistentAgent | None) -> bool:
    if agent is None:
        return False
    if getattr(agent, "planning_state", None) == PersistentAgent.PlanningState.PLANNING:
        return False
    return (
        getattr(agent, "signup_preview_state", None)
        == PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
    )


def transition_agent_to_signup_preview_waiting(agent_id) -> bool:
    transitioned = bool(
        PersistentAgent.objects.filter(
            id=agent_id,
            signup_preview_state=PersistentAgent.SignupPreviewState.AWAITING_FIRST_REPLY_PAUSE,
        )
        .exclude(planning_state=PersistentAgent.PlanningState.PLANNING)
        .update(
            signup_preview_state=PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION,
        )
    )
    if not transitioned:
        return False

    agent = PersistentAgent.objects.filter(id=agent_id).only("id", "user_id", "signup_preview_state").first()
    if agent is None or agent.user_id is None:
        return True

    transaction.on_commit(
        lambda: Analytics.track_event(
            user_id=agent.user_id,
            event=AnalyticsEvent.SIGNUP_PREVIEW_PAUSED_AFTER_FIRST_REPLY,
            source=AnalyticsSource.WEB,
            properties={
                "agent_id": str(agent.id),
                "signup_preview_state": agent.signup_preview_state,
                "source": "first_reply",
            },
        )
    )
    return True


def resume_signup_preview_agent_if_eligible(
    agent: PersistentAgent,
    user,
    *,
    resume_source: str = "unknown",
    plan_before: str | None = None,
    plan_after: str | None = None,
) -> SignupPreviewResumeResult:
    if agent.organization_id is not None:
        return SignupPreviewResumeResult()
    if agent.user_id != getattr(user, "id", None):
        return SignupPreviewResumeResult()
    if not is_signup_preview_state_active(agent):
        return SignupPreviewResumeResult()

    result = _resume_signup_preview_agents_if_user_eligible(
        PersistentAgent.objects.filter(
            id=agent.id,
            user_id=user.id,
            organization__isnull=True,
            signup_preview_state__in=ACTIVE_SIGNUP_PREVIEW_STATES,
        ),
        user,
        reconcile_plan=True,
        resume_source=resume_source,
        plan_before=plan_before,
        plan_after=plan_after,
    )
    if not result.includes(agent):
        return SignupPreviewResumeResult()

    agent.signup_preview_state = PersistentAgent.SignupPreviewState.NONE
    return result


def resume_signup_preview_agents_for_user_if_eligible(
    user,
    *,
    reconcile_plan: bool = True,
    resume_source: str = "unknown",
    plan_before: str | None = None,
    plan_after: str | None = None,
) -> SignupPreviewResumeResult:
    if getattr(user, "id", None) is None:
        return SignupPreviewResumeResult()

    return _resume_signup_preview_agents_if_user_eligible(
        PersistentAgent.objects.filter(
            user_id=user.id,
            organization__isnull=True,
            signup_preview_state__in=ACTIVE_SIGNUP_PREVIEW_STATES,
        ),
        user,
        reconcile_plan=reconcile_plan,
        resume_source=resume_source,
        plan_before=plan_before,
        plan_after=plan_after,
    )
