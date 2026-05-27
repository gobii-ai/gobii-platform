"""Shared organization role policies."""

from api.models import OrganizationMembership


ORG_AGENT_CONFIG_AUTHORITY_ROLES = (
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
)

