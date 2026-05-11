from django.db import transaction

from api.models import (
    Organization,
    OrganizationMembership,
    SolutionPartnerMember,
)


def approved_solution_partner_memberships_for_user(user):
    return (
        SolutionPartnerMember.objects.filter(
            user=user,
            is_active=True,
            solution_partner__is_approved=True,
        )
        .select_related("solution_partner")
        .order_by("solution_partner__name")
    )


def user_has_solution_partner_portal_access(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return approved_solution_partner_memberships_for_user(user).exists()


def client_organizations_for_partner_memberships(partner_memberships):
    partner_ids = [membership.solution_partner_id for membership in partner_memberships]
    return (
        Organization.objects.filter(
            managed_by_solution_partner_id__in=partner_ids,
            is_active=True,
        )
        .select_related("managed_by_solution_partner")
        .order_by("name")
    )


def user_can_access_client_organization_through_partner(user, organization: Organization) -> bool:
    partner_id = organization.managed_by_solution_partner_id
    if not partner_id:
        return False
    return approved_solution_partner_memberships_for_user(user).filter(
        solution_partner_id=partner_id,
    ).exists()


@transaction.atomic
def ensure_solution_partner_client_membership(user, organization: Organization) -> OrganizationMembership:
    partner = organization.managed_by_solution_partner
    if partner is None:
        raise ValueError("Organization is not managed by a Solutions Partner.")
    if not user_can_access_client_organization_through_partner(user, organization):
        raise PermissionError("User is not an approved member of this Solutions Partner.")

    membership, created = OrganizationMembership.objects.get_or_create(
        org=organization,
        user=user,
        defaults={
            "role": OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
            "status": OrganizationMembership.OrgStatus.ACTIVE,
            "source_solution_partner": partner,
        },
    )
    if created:
        return membership

    updates = []
    direct_admin_roles = {
        OrganizationMembership.OrgRole.OWNER,
        OrganizationMembership.OrgRole.ADMIN,
    }
    was_inactive = membership.status != OrganizationMembership.OrgStatus.ACTIVE
    if was_inactive:
        membership.status = OrganizationMembership.OrgStatus.ACTIVE
        updates.append("status")
        if membership.role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER:
            membership.role = OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
            updates.append("role")
        if membership.source_solution_partner_id != partner.id:
            membership.source_solution_partner = partner
            updates.append("source_solution_partner")
    elif membership.source_solution_partner_id == partner.id:
        if membership.role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER:
            membership.role = OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
            updates.append("role")
    elif membership.role == OrganizationMembership.OrgRole.SOLUTIONS_PARTNER:
        membership.source_solution_partner = partner
        updates.append("source_solution_partner")
    elif membership.role not in direct_admin_roles:
        membership.role = OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
        membership.source_solution_partner = partner
        updates.extend(["role", "source_solution_partner"])

    if updates:
        membership.save(update_fields=updates)
    return membership
