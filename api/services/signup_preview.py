from api.models import PersistentAgent
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
    if not can_user_use_personal_agents_and_api(user):
        return False

    updated = PersistentAgent.objects.filter(id=agent.id).exclude(
        signup_preview_state=PersistentAgent.SignupPreviewState.NONE,
    ).update(
        signup_preview_state=PersistentAgent.SignupPreviewState.NONE,
    )
    if not updated:
        return False

    agent.signup_preview_state = PersistentAgent.SignupPreviewState.NONE
    return True
