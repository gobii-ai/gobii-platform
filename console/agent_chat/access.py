from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet

from api.models import OrganizationMembership, PersistentAgent
from console.context_overrides import resolve_context_override


def _has_active_org_membership(user, org_id) -> bool:
    if not org_id:
        return False
    return OrganizationMembership.objects.filter(
        user=user,
        org_id=org_id,
        status=OrganizationMembership.OrgStatus.ACTIVE,
    ).exists()


def _resolve_agent_context(user, queryset: QuerySet, agent_id: str | None):
    if not agent_id:
        return None
    try:
        agent_record = queryset.filter(pk=agent_id).values_list("organization_id", "user_id").first()
    except (TypeError, ValueError):
        return None
    if not agent_record:
        return None
    org_id, owner_id = agent_record
    if org_id:
        membership = OrganizationMembership.objects.select_related("org").filter(
            user=user,
            org_id=org_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).first()
        if membership:
            return ("organization", membership)
        return None
    if owner_id == user.id:
        return ("personal", None)
    return None


def _sync_session_context(session, membership: OrganizationMembership) -> None:
    if session is None or membership is None:
        return
    session["context_type"] = "organization"
    session["context_id"] = str(membership.org.id)
    session["context_name"] = membership.org.name


def _sync_personal_context(session, user) -> None:
    if session is None or user is None:
        return
    session["context_type"] = "personal"
    session["context_id"] = str(user.id)
    session["context_name"] = user.get_full_name() or user.username or user.email or "Personal"


def agent_queryset_for(
    user,
    session,
    agent_id: str | None = None,
    sync_session: bool = False,
    context_override: dict | None = None,
) -> QuerySet:
    """Return queryset of agents visible to the user within the console context."""
    qs = PersistentAgent.objects.non_eval().select_related("browser_use_agent").all()
    if context_override is None and session is not None:
        context_override = getattr(session, "_context_override", None)
    if context_override is not None:
        context, membership = resolve_context_override(user, context_override)
        if context.type == "organization":
            if sync_session and membership:
                _sync_session_context(session, membership)
            return qs.filter(organization_id=context.id)
        if sync_session:
            _sync_personal_context(session, user)
        return qs.filter(user=user, organization__isnull=True)

    resolved_context = _resolve_agent_context(user, qs, agent_id)
    if resolved_context:
        context_type, membership = resolved_context
        if context_type == "organization" and membership:
            if sync_session:
                _sync_session_context(session, membership)
            return qs.filter(organization_id=membership.org_id)
        if context_type == "personal":
            if sync_session:
                _sync_personal_context(session, user)
            return qs.filter(user=user, organization__isnull=True)

    context_type = (session or {}).get("context_type", "personal") if session is not None else "personal"
    if context_type == "organization":
        org_id = (session or {}).get("context_id")
        if not _has_active_org_membership(user, org_id):
            raise PermissionDenied("Not authorized for this organization")
        return qs.filter(organization_id=org_id)

    return qs.filter(user=user, organization__isnull=True)


def resolve_agent(
    user,
    session,
    agent_id: str,
    sync_session: bool = False,
    context_override: dict | None = None,
) -> PersistentAgent:
    queryset = agent_queryset_for(
        user,
        session,
        agent_id=agent_id,
        sync_session=sync_session,
        context_override=context_override,
    )
    try:
        return queryset.get(pk=agent_id)
    except PersistentAgent.DoesNotExist as exc:  # pragma: no cover - defensive guard
        raise PermissionDenied("Agent not found in current context") from exc
