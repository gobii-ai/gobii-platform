from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from api.models import PersistentAgent
from console.context_helpers import ConsoleContext, resolve_console_context
from console.context_overrides import get_context_override


def agent_queryset_for(user, context: ConsoleContext) -> QuerySet:
    """Return queryset of agents visible to the user within the console context."""
    qs = PersistentAgent.objects.non_eval().select_related("browser_use_agent").all()
    if context.type == "organization":
        return qs.filter(organization_id=context.id)
    return qs.filter(user=user, organization__isnull=True)


def resolve_agent(
    user,
    session,
    agent_id: str,
    context_override: dict | None = None,
) -> PersistentAgent:
    context_info = resolve_console_context(user, session, override=context_override)
    queryset = agent_queryset_for(user, context_info.current_context)
    try:
        return queryset.get(pk=agent_id)
    except PersistentAgent.DoesNotExist as exc:  # pragma: no cover - defensive guard
        raise PermissionDenied("Agent not found in current context") from exc


def resolve_agent_for_request(request, agent_id: str) -> PersistentAgent:
    context_override = get_context_override(request)
    return resolve_agent(request.user, request.session, agent_id, context_override=context_override)
