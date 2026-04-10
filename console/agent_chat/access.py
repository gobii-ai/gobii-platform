from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from api.models import OrganizationMembership, PersistentAgent, AgentCollaborator
from api.services.signup_preview import user_can_access_signup_preview_agent
from console.context_helpers import ConsoleContext, resolve_console_context
from console.context_overrides import get_context_override
from console.role_constants import MEMBER_MANAGE_ROLES
from util.trial_enforcement import (
    PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE,
    can_user_access_personal_agent_chat,
    can_user_use_personal_agents_and_api,
)


def _can_access_personal_agent(user, *, allow_delinquent_personal_chat: bool = False) -> bool:
    if allow_delinquent_personal_chat:
        return can_user_access_personal_agent_chat(user)
    return can_user_use_personal_agents_and_api(user)


def _is_blocked_personal_owner(
    user,
    agent: PersistentAgent,
    *,
    allow_delinquent_personal_chat: bool = False,
) -> bool:
    if user_can_access_signup_preview_agent(agent, user):
        return False
    return bool(
        agent.organization_id is None
        and agent.user_id == user.id
        and not _can_access_personal_agent(
            user,
            allow_delinquent_personal_chat=allow_delinquent_personal_chat,
        )
    )


def agent_queryset_for(
    user,
    context: ConsoleContext,
    *,
    allow_delinquent_personal_chat: bool = False,
) -> QuerySet:
    """Return queryset of agents visible to the user within the console context."""
    qs = PersistentAgent.objects.non_eval().alive().select_related("browser_use_agent")
    if context.type == "organization":
        return qs.filter(organization_id=context.id)
    access_allowed = _can_access_personal_agent(
        user,
        allow_delinquent_personal_chat=allow_delinquent_personal_chat,
    )
    personal_qs = qs.filter(user=user, organization__isnull=True)
    if access_allowed:
        return personal_qs
    return personal_qs.exclude(signup_preview_state=PersistentAgent.SignupPreviewState.NONE)

def shared_agent_queryset_for(user) -> QuerySet:
    return (
        PersistentAgent.objects
        .non_eval()
        .alive()
        .select_related("browser_use_agent")
        .filter(collaborators__user=user)
    )

def user_can_manage_agent(
    user,
    agent: PersistentAgent,
    *,
    allow_delinquent_personal_chat: bool = False,
) -> bool:
    if user.is_staff:
        return True
    if agent.user_id == user.id:
        if _is_blocked_personal_owner(
            user,
            agent,
            allow_delinquent_personal_chat=allow_delinquent_personal_chat,
        ):
            return False
        return True
    if agent.organization_id:
        return OrganizationMembership.objects.filter(
            user=user,
            org_id=agent.organization_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).exists()
    return False


def user_can_manage_agent_settings(
    user,
    agent: PersistentAgent,
    *,
    allow_delinquent_personal_chat: bool = False,
) -> bool:
    if user.is_staff:
        return True
    if agent.user_id == user.id:
        return not _is_blocked_personal_owner(
            user,
            agent,
            allow_delinquent_personal_chat=allow_delinquent_personal_chat,
        )
    if agent.organization_id:
        return OrganizationMembership.objects.filter(
            user=user,
            org_id=agent.organization_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=MEMBER_MANAGE_ROLES,
        ).exists()
    return False

def user_is_collaborator(user, agent: PersistentAgent) -> bool:
    return AgentCollaborator.objects.filter(agent=agent, user=user).exists()


def resolve_agent(
    user,
    session,
    agent_id: str,
    context_override: dict | None = None,
    allow_shared: bool = False,
    allow_delinquent_personal_chat: bool = False,
) -> PersistentAgent:
    context_info = resolve_console_context(user, session, override=context_override)
    queryset = agent_queryset_for(
        user,
        context_info.current_context,
        allow_delinquent_personal_chat=allow_delinquent_personal_chat,
    )
    try:
        agent = queryset.get(pk=agent_id)
        if _is_blocked_personal_owner(
            user,
            agent,
            allow_delinquent_personal_chat=allow_delinquent_personal_chat,
        ):
            raise PermissionDenied(PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE)
        return agent
    except PersistentAgent.DoesNotExist as exc:  # pragma: no cover - defensive guard
        agent = (
            PersistentAgent.objects
            .non_eval()
            .alive()
            .select_related("browser_use_agent")
            .filter(pk=agent_id)
            .first()
        )
        if agent:
            if _is_blocked_personal_owner(
                user,
                agent,
                allow_delinquent_personal_chat=allow_delinquent_personal_chat,
            ):
                raise PermissionDenied(PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE) from exc
            if user_can_manage_agent(
                user,
                agent,
                allow_delinquent_personal_chat=allow_delinquent_personal_chat,
            ):
                return agent
            if allow_shared and user_is_collaborator(user, agent):
                return agent
            raise PermissionDenied("Not permitted to access this agent.") from exc
        raise PermissionDenied("Agent not found.") from exc


def resolve_agent_for_request(
    request,
    agent_id: str,
    *,
    allow_shared: bool = False,
    allow_delinquent_personal_chat: bool = False,
) -> PersistentAgent:
    context_override = get_context_override(request)
    return resolve_agent(
        request.user,
        request.session,
        agent_id,
        context_override=context_override,
        allow_shared=allow_shared,
        allow_delinquent_personal_chat=allow_delinquent_personal_chat,
    )


def resolve_manageable_agent(
    user,
    session,
    agent_id: str,
    context_override: dict | None = None,
    *,
    allow_delinquent_personal_chat: bool = False,
) -> PersistentAgent:
    agent = resolve_agent(
        user,
        session,
        agent_id,
        context_override=context_override,
        allow_shared=False,
        allow_delinquent_personal_chat=allow_delinquent_personal_chat,
    )
    if not user_can_manage_agent_settings(
        user,
        agent,
        allow_delinquent_personal_chat=allow_delinquent_personal_chat,
    ):
        raise PermissionDenied("Not permitted to manage this agent.")
    return agent


def resolve_manageable_agent_for_request(
    request,
    agent_id: str,
    *,
    allow_delinquent_personal_chat: bool = False,
) -> PersistentAgent:
    context_override = get_context_override(request)
    return resolve_manageable_agent(
        request.user,
        request.session,
        agent_id,
        context_override=context_override,
        allow_delinquent_personal_chat=allow_delinquent_personal_chat,
    )
