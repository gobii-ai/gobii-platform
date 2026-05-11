from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from waffle.models import Flag

from api.models import (
    Organization,
    OrganizationInvite,
    OrganizationMembership,
    SolutionPartner,
    SolutionPartnerMember,
)


@tag("batch_organizations")
class SolutionPartnerPortalTest(TestCase):
    def setUp(self):
        Flag.objects.update_or_create(name="organizations", defaults={"everyone": True})

        User = get_user_model()
        self.partner_user = User.objects.create_user(
            email="partner@example.com",
            password="pw",
            username="partner",
        )
        self.other_partner_user = User.objects.create_user(
            email="other-partner@example.com",
            password="pw",
            username="other-partner",
        )
        self.client_user = User.objects.create_user(
            email="client@example.com",
            password="pw",
            username="client",
        )
        self.outsider = User.objects.create_user(
            email="outsider-sp@example.com",
            password="pw",
            username="outsider-sp",
        )

    def _create_partner(self, *, user=None, name="ABC Consulting", approved=True):
        partner = SolutionPartner.objects.create(
            name=name,
            is_approved=approved,
            created_by=user or self.partner_user,
        )
        member = SolutionPartnerMember.objects.create(
            solution_partner=partner,
            user=user or self.partner_user,
        )
        return partner, member

    def _create_client_org(self, partner, *, name="Client Org", slug="client-org"):
        return Organization.objects.create(
            name=name,
            slug=slug,
            created_by=self.partner_user,
            managed_by_solution_partner=partner,
        )

    @tag("batch_organizations")
    def test_unapproved_users_cannot_access_partner_portal_or_switch(self):
        partner, _member = self._create_partner(approved=False)
        org = self._create_client_org(partner)

        self.client.force_login(self.partner_user)
        self.assertEqual(
            self.client.get(reverse("solution_partner_portal")).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("solution_partner_client_switch", kwargs={"org_id": org.id})
            ).status_code,
            403,
        )

        self.client.force_login(self.outsider)
        self.assertEqual(
            self.client.get(reverse("solution_partner_portal")).status_code,
            403,
        )

    @tag("batch_organizations")
    def test_approved_partner_member_can_create_managed_client_org(self):
        partner, _member = self._create_partner()

        self.client.force_login(self.partner_user)
        response = self.client.post(
            reverse("solution_partner_portal"),
            {"name": "Tire Shop Client", "solution_partner": str(partner.id)},
        )

        org = Organization.objects.get(name="Tire Shop Client")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("organization_detail", kwargs={"org_id": org.id}),
        )
        self.assertEqual(org.managed_by_solution_partner, partner)

        membership = OrganizationMembership.objects.get(org=org, user=self.partner_user)
        self.assertEqual(membership.role, OrganizationMembership.OrgRole.SOLUTIONS_PARTNER)
        self.assertEqual(membership.status, OrganizationMembership.OrgStatus.ACTIVE)
        self.assertEqual(membership.source_solution_partner, partner)
        self.assertEqual(org.billing.seats_reserved, 0)

    @tag("batch_organizations")
    def test_partner_member_lists_only_managed_client_organizations(self):
        partner, _member = self._create_partner()
        client_org = self._create_client_org(partner, name="Alpha Client", slug="alpha-client")

        other_partner, _other_member = self._create_partner(
            user=self.other_partner_user,
            name="Other Consulting",
        )
        self._create_client_org(other_partner, name="Beta Client", slug="beta-client")

        self.client.force_login(self.partner_user)
        response = self.client.get(reverse("solution_partner_portal"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["client_organizations"]), [client_org])

    @tag("batch_organizations")
    def test_partner_member_can_switch_to_managed_client_org(self):
        partner, _member = self._create_partner()
        org = self._create_client_org(partner)

        self.client.force_login(self.partner_user)
        response = self.client.post(
            reverse("solution_partner_client_switch", kwargs={"org_id": org.id})
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("organization_detail", kwargs={"org_id": org.id}),
        )
        membership = OrganizationMembership.objects.get(org=org, user=self.partner_user)
        self.assertEqual(membership.role, OrganizationMembership.OrgRole.SOLUTIONS_PARTNER)
        self.assertEqual(membership.source_solution_partner, partner)
        self.assertEqual(self.client.session["context_type"], "organization")
        self.assertEqual(self.client.session["context_id"], str(org.id))

        detail_response = self.client.get(reverse("organization_detail", kwargs={"org_id": org.id}))
        self.assertEqual(detail_response.status_code, 200)

    @tag("batch_organizations")
    def test_partner_switch_upgrades_existing_lower_role_membership(self):
        partner, _member = self._create_partner()
        org = self._create_client_org(partner)
        OrganizationMembership.objects.create(
            org=org,
            user=self.client_user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        OrganizationMembership.objects.create(
            org=org,
            user=self.partner_user,
            role=OrganizationMembership.OrgRole.VIEWER,
        )
        billing = org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])

        self.client.force_login(self.partner_user)
        response = self.client.post(
            reverse("solution_partner_client_switch", kwargs={"org_id": org.id})
        )

        self.assertEqual(response.status_code, 302)
        membership = OrganizationMembership.objects.get(org=org, user=self.partner_user)
        self.assertEqual(membership.role, OrganizationMembership.OrgRole.SOLUTIONS_PARTNER)
        self.assertEqual(membership.source_solution_partner, partner)
        detail_url = reverse("organization_detail", kwargs={"org_id": org.id})
        invite_response = self.client.post(
            detail_url,
            {"email": "new-client-user@example.com", "role": OrganizationMembership.OrgRole.MEMBER},
        )
        self.assertEqual(invite_response.status_code, 302)
        self.assertTrue(
            OrganizationInvite.objects.filter(
                org=org,
                email__iexact="new-client-user@example.com",
            ).exists()
        )

    @tag("batch_organizations")
    def test_partner_member_cannot_switch_to_unassociated_client_org(self):
        self._create_partner()
        other_partner, _other_member = self._create_partner(
            user=self.other_partner_user,
            name="Other Consulting",
        )
        other_org = self._create_client_org(other_partner, name="Other Client", slug="other-client")

        self.client.force_login(self.partner_user)
        response = self.client.post(
            reverse("solution_partner_client_switch", kwargs={"org_id": other_org.id})
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            OrganizationMembership.objects.filter(org=other_org, user=self.partner_user).exists()
        )

    @tag("batch_organizations")
    def test_non_partner_user_cannot_access_managed_org_unless_invited(self):
        partner, _member = self._create_partner()
        org = self._create_client_org(partner)
        detail_url = reverse("organization_detail", kwargs={"org_id": org.id})

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(detail_url).status_code, 403)

        OrganizationMembership.objects.create(
            org=org,
            user=self.outsider,
            role=OrganizationMembership.OrgRole.MEMBER,
        )
        self.assertEqual(self.client.get(detail_url).status_code, 200)

    @tag("batch_organizations")
    def test_existing_org_admin_behavior_still_works(self):
        org = Organization.objects.create(name="Direct Client", slug="direct-client", created_by=self.client_user)
        OrganizationMembership.objects.create(
            org=org,
            user=self.client_user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        billing = org.billing
        billing.purchased_seats = 1
        billing.save(update_fields=["purchased_seats"])

        self.client.force_login(self.client_user)
        detail_url = reverse("organization_detail", kwargs={"org_id": org.id})
        self.assertEqual(self.client.get(detail_url).status_code, 200)

        response = self.client.post(
            detail_url,
            {"email": "employee@example.com", "role": OrganizationMembership.OrgRole.MEMBER},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            OrganizationInvite.objects.filter(
                org=org,
                email__iexact="employee@example.com",
                role=OrganizationMembership.OrgRole.MEMBER,
            ).exists()
        )

    @tag("batch_organizations")
    def test_solutions_partner_membership_does_not_reserve_billable_seat(self):
        partner, _member = self._create_partner()
        org = Organization.objects.create(
            name="Billing Client",
            slug="billing-client",
            created_by=self.client_user,
            managed_by_solution_partner=partner,
        )
        OrganizationMembership.objects.create(
            org=org,
            user=self.client_user,
            role=OrganizationMembership.OrgRole.OWNER,
        )
        OrganizationMembership.objects.create(
            org=org,
            user=self.partner_user,
            role=OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
            source_solution_partner=partner,
        )

        self.assertEqual(org.billing.seats_reserved, 0)

    @tag("batch_organizations")
    def test_deactivating_partner_member_revokes_partner_sourced_org_access(self):
        partner, member = self._create_partner()
        org = self._create_client_org(partner)

        self.client.force_login(self.partner_user)
        self.client.post(reverse("solution_partner_client_switch", kwargs={"org_id": org.id}))

        membership = OrganizationMembership.objects.get(org=org, user=self.partner_user)
        self.assertEqual(membership.status, OrganizationMembership.OrgStatus.ACTIVE)

        member.is_active = False
        member.save(update_fields=["is_active"])

        membership.refresh_from_db()
        self.assertEqual(membership.status, OrganizationMembership.OrgStatus.REMOVED)
        self.assertEqual(
            self.client.post(
                reverse("solution_partner_client_switch", kwargs={"org_id": org.id})
            ).status_code,
            403,
        )

    @tag("batch_organizations")
    def test_disapproving_partner_revokes_partner_sourced_org_access(self):
        partner, _member = self._create_partner()
        org = self._create_client_org(partner)

        self.client.force_login(self.partner_user)
        self.client.post(reverse("solution_partner_client_switch", kwargs={"org_id": org.id}))

        partner.is_approved = False
        partner.save(update_fields=["is_approved"])

        membership = OrganizationMembership.objects.get(org=org, user=self.partner_user)
        self.assertEqual(membership.status, OrganizationMembership.OrgStatus.REMOVED)
        self.assertEqual(
            self.client.get(reverse("solution_partner_portal")).status_code,
            403,
        )

    @tag("batch_organizations")
    def test_reassigning_client_org_revokes_previous_partner_access(self):
        partner, _member = self._create_partner()
        other_partner, _other_member = self._create_partner(
            user=self.other_partner_user,
            name="Other Consulting",
        )
        org = self._create_client_org(partner)

        self.client.force_login(self.partner_user)
        self.client.post(reverse("solution_partner_client_switch", kwargs={"org_id": org.id}))

        org.managed_by_solution_partner = other_partner
        org.save(update_fields=["managed_by_solution_partner"])

        membership = OrganizationMembership.objects.get(org=org, user=self.partner_user)
        self.assertEqual(membership.status, OrganizationMembership.OrgStatus.REMOVED)
        self.assertEqual(
            self.client.get(reverse("organization_detail", kwargs={"org_id": org.id})).status_code,
            403,
        )
