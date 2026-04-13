from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase, tag

from api.admin import OrganizationBillingAdmin, UserBillingAdmin
from api.models import OrganizationBilling, UserBilling


@tag("batch_billing")
class BillingAdminTests(SimpleTestCase):
    def setUp(self):
        admin_site = AdminSite()
        self.user_billing_admin = UserBillingAdmin(UserBilling, admin_site)
        self.organization_billing_admin = OrganizationBillingAdmin(OrganizationBilling, admin_site)

    def test_user_billing_admin_exposes_plan_version(self):
        self.assertIn("plan_version", self.user_billing_admin.autocomplete_fields)
        self.assertIn("plan_version", self.user_billing_admin.list_display)

        primary_fields = self.user_billing_admin.fieldsets[0][1]["fields"]
        self.assertIn("plan_version", primary_fields)

    def test_organization_billing_admin_exposes_plan_version(self):
        self.assertIn("plan_version", self.organization_billing_admin.autocomplete_fields)
        self.assertIn("plan_version", self.organization_billing_admin.list_display)
