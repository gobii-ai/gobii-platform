from django.contrib.auth.models import AbstractBaseUser

from api.models import OrganizationMembership, PersistentAgent
from console.agent_chat.access import user_is_collaborator


def _display_name_for_user(user: AbstractBaseUser) -> str:
    return user.get_full_name() or user.username or user.email or "Personal"


def _build_personal_context(user: AbstractBaseUser, user_id: str) -> dict[str, str]:
    return {
        "type": "personal",
        "id": str(user_id),
        "name": _display_name_for_user(user),
    }


def _build_organization_context(org_id: str, org_name: str) -> dict[str, str]:
    return {
        "type": "organization",
        "id": str(org_id),
        "name": org_name,
    }


def resolve_context_override_for_agent(
    user: AbstractBaseUser,
    agent_id: str,
    *,
    include_deleted: bool = False,
) -> tuple[dict[str, str] | None, str | None]:
    """
    Resolve the effective console context for a given agent.

    Returns a tuple of (override, error_code), where error_code is one of:
    - "not_found"
    - "forbidden"
    - "deleted" (only when include_deleted=True)
    - None
    """
    try:
        queryset = PersistentAgent.objects.non_eval().select_related("organization")
        if not include_deleted:
            queryset = queryset.alive()
        agent = queryset.get(pk=agent_id)
    except PersistentAgent.DoesNotExist:
        return None, "not_found"

    deleted_code = "deleted" if include_deleted and agent.is_deleted else None

    if agent.organization_id:
        membership = (
            OrganizationMembership.objects.select_related("org")
            .filter(
                user=user,
                org_id=agent.organization_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            )
            .first()
        )
        if membership:
            return (
                _build_organization_context(agent.organization_id, membership.org.name),
                deleted_code,
            )
        if deleted_code and (user.is_staff or user.is_superuser):
            org_name = agent.organization.name if agent.organization is not None else "Organization"
            return _build_organization_context(agent.organization_id, org_name), deleted_code
    elif agent.user_id == user.id:
        return _build_personal_context(user, agent.user_id), deleted_code
    elif deleted_code and (user.is_staff or user.is_superuser):
        return _build_personal_context(agent.user, agent.user_id), deleted_code

    if user_is_collaborator(user, agent):
        return None, deleted_code

    return None, "forbidden"
