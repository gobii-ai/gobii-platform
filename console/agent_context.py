from django.contrib.auth.models import AbstractBaseUser

from api.models import OrganizationMembership, PersistentAgent
from console.agent_chat.access import user_is_collaborator


def resolve_context_override_for_agent(
    user: AbstractBaseUser,
    agent_id: str,
) -> tuple[dict[str, str] | None, str | None]:
    """
    Resolve the effective console context for a given agent.

    Returns a tuple of (override, error_code), where error_code is one of:
    - "not_found"
    - "forbidden"
    - None
    """
    try:
        agent = (
            PersistentAgent.objects.non_eval()
            .select_related("organization")
            .get(pk=agent_id)
        )
    except PersistentAgent.DoesNotExist:
        return None, "not_found"

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
        if membership is None:
            if user_is_collaborator(user, agent):
                return None, None
            return None, "forbidden"
        return (
            {
                "type": "organization",
                "id": str(agent.organization_id),
                "name": membership.org.name,
            },
            None,
        )

    if agent.user_id != user.id:
        if user_is_collaborator(user, agent):
            return None, None
        return None, "forbidden"

    return (
        {
            "type": "personal",
            "id": str(agent.user_id),
            "name": user.get_full_name() or user.username or user.email or "Personal",
        },
        None,
    )
