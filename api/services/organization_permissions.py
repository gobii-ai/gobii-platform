"""Shared organization role policies."""

from api.models import OrganizationMembership


ORG_AGENT_CONFIG_AUTHORITY_ROLES = (
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
)

ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS = "members_can_create_agents"


def organization_members_can_create_agents(organization) -> bool:
    org_settings = organization.org_settings if organization is not None else {}
    if not isinstance(org_settings, dict):
        return False
    return org_settings.get(ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS) is True


def user_role_can_create_org_agents(role: str | None, organization) -> bool:
    if role in ORG_AGENT_CONFIG_AUTHORITY_ROLES:
        return True
    return (
        role == OrganizationMembership.OrgRole.MEMBER
        and organization_members_can_create_agents(organization)
    )
