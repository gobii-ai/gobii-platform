from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from api.models import OrganizationMembership, PersistentAgent


def _has_active_org_membership(user, org_id) -> bool:
    if not org_id:
        return False
    return OrganizationMembership.objects.filter(
        user=user,
        org_id=org_id,
        status=OrganizationMembership.OrgStatus.ACTIVE,
    ).exists()


def _resolve_org_from_agent(user, queryset: QuerySet, agent_id: str | None) -> UUID | None:
    if not agent_id:
        return None
    try:
        org_id = queryset.filter(pk=agent_id).values_list("organization_id", flat=True).first()
    except (TypeError, ValueError):
        return None
    if org_id and _has_active_org_membership(user, org_id):
        return org_id
    return None


def _sync_session_context(session, membership: OrganizationMembership) -> None:
    if session is None or membership is None:
        return
    session["context_type"] = "organization"
    session["context_id"] = str(membership.org.id)
    session["context_name"] = membership.org.name


def agent_queryset_for(user, session, agent_id: str | None = None) -> QuerySet:
    """Return queryset of agents visible to the user within the console context."""
    qs = PersistentAgent.objects.non_eval().select_related("browser_use_agent").all()
    context_type = (session or {}).get("context_type", "personal") if session is not None else "personal"

    if context_type == "organization":
        org_id = (session or {}).get("context_id")
        if not _has_active_org_membership(user, org_id):
            raise PermissionDenied("Not authorized for this organization")
        return qs.filter(organization_id=org_id)

    fallback_org_id = _resolve_org_from_agent(user, qs, agent_id)
    if fallback_org_id:
        membership = OrganizationMembership.objects.select_related("org").filter(
            user=user,
            org_id=fallback_org_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).first()
        if membership:
            _sync_session_context(session, membership)
        return qs.filter(organization_id=fallback_org_id)

    return qs.filter(user=user, organization__isnull=True)


def resolve_agent(user, session, agent_id: str) -> PersistentAgent:
    queryset = agent_queryset_for(user, session, agent_id=agent_id)
    try:
        return queryset.get(pk=agent_id)
    except PersistentAgent.DoesNotExist as exc:  # pragma: no cover - defensive guard
        raise PermissionDenied("Agent not found in current context") from exc
