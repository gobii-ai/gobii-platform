from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from api.models import OrganizationMembership, PersistentAgent, AgentCollaborator
from console.context_helpers import ConsoleContext, resolve_console_context
from console.context_overrides import get_context_override


def agent_queryset_for(user, context: ConsoleContext) -> QuerySet:
    """Return queryset of agents visible to the user within the console context."""
    qs = PersistentAgent.objects.non_eval().select_related("browser_use_agent").all()
    if context.type == "organization":
        return qs.filter(organization_id=context.id)
    return qs.filter(user=user, organization__isnull=True)

def shared_agent_queryset_for(user) -> QuerySet:
    return (
        PersistentAgent.objects
        .non_eval()
        .select_related("browser_use_agent")
        .filter(collaborators__user=user)
    )

def user_can_manage_agent(user, agent: PersistentAgent) -> bool:
    if user.is_staff:
        return True
    if agent.user_id == user.id:
        return True
    if agent.organization_id:
        return OrganizationMembership.objects.filter(
            user=user,
            org_id=agent.organization_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
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
) -> PersistentAgent:
    context_info = resolve_console_context(user, session, override=context_override)
    queryset = agent_queryset_for(user, context_info.current_context)
    try:
        return queryset.get(pk=agent_id)
    except PersistentAgent.DoesNotExist as exc:  # pragma: no cover - defensive guard
        if allow_shared:
            agent = shared_agent_queryset_for(user).filter(pk=agent_id).first()
            if agent:
                return agent
        raise PermissionDenied("Agent not found in current context") from exc


def resolve_agent_for_request(request, agent_id: str, *, allow_shared: bool = False) -> PersistentAgent:
    context_override = get_context_override(request)
    return resolve_agent(
        request.user,
        request.session,
        agent_id,
        context_override=context_override,
        allow_shared=allow_shared,
    )
