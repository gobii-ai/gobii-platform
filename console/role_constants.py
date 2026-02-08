from django.conf import settings

from api.models import OrganizationMembership

# Keep role sets in a standalone module so both views and service helpers can
# depend on them without importing console.views (which is very large and can
# cause import cycles).
BILLING_MANAGE_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
    OrganizationMembership.OrgRole.BILLING,
}
if settings.SOLUTIONS_PARTNER_BILLING_ACCESS:
    BILLING_MANAGE_ROLES.add(OrganizationMembership.OrgRole.SOLUTIONS_PARTNER)

