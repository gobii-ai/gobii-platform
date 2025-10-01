from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from api.models import OrganizationMembership, PersistentAgent


def agent_queryset_for(user, session) -> QuerySet:
    """Return queryset of agents visible to the user within the console context."""
    qs = PersistentAgent.objects.select_related("browser_use_agent").all()
    context_type = (session or {}).get("context_type", "personal") if session is not None else "personal"

    if context_type == "organization":
        org_id = (session or {}).get("context_id")
        if not OrganizationMembership.objects.filter(
            user=user,
            org_id=org_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).exists():
            raise PermissionDenied("Not authorized for this organization")
        return qs.filter(organization_id=org_id)

    return qs.filter(user=user, organization__isnull=True)


def resolve_agent(user, session, agent_id: str) -> PersistentAgent:
    queryset = agent_queryset_for(user, session)
    try:
        return queryset.get(pk=agent_id)
    except PersistentAgent.DoesNotExist as exc:  # pragma: no cover - defensive guard
        raise PermissionDenied("Agent not found in current context") from exc
