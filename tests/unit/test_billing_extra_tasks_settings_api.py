import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from api.models import Organization, OrganizationMembership
from constants.plans import EXTRA_TASKS_DEFAULT_MAX_TASKS


@tag("batch_billing")
class BillingExtraTasksSettingsApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="billing-settings-user",
            email="billing-settings-user@example.com",
            password="pw12345",
        )
        self.org = Organization.objects.create(
            name="Billing Settings Org",
            slug="billing-settings-org",
            created_by=self.user,
        )
        self.membership = OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.client.force_login(self.user)

        self.update_url = reverse("update_billing_settings")
        self.load_url = reverse("get_billing_settings")

    def _set_personal_context(self):
        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.user.id)
        session["context_name"] = self.user.get_full_name() or self.user.email
        session.save()

    def _set_org_context(self):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

    def test_personal_update_returns_derived_extra_tasks_settings(self):
        self._set_personal_context()

        resp = self.client.post(
            self.update_url,
            data=json.dumps({"enabled": True, "infinite": False, "maxTasks": 42}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("max_extra_tasks"), 42)

        settings = payload.get("extra_tasks") or {}
        self.assertTrue(settings.get("enabled"))
        self.assertFalse(settings.get("infinite"))
        self.assertEqual(settings.get("configuredLimit"), 42)
        self.assertEqual(settings.get("maxTasks"), 42)
        self.assertTrue(settings.get("canModify"))
        self.assertIn("endpoints", settings)

    def test_personal_disable_uses_default_max_tasks_for_ui(self):
        self._set_personal_context()

        resp = self.client.post(
            self.update_url,
            data=json.dumps({"enabled": False, "infinite": False, "maxTasks": 5}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("max_extra_tasks"), 0)

        settings = payload.get("extra_tasks") or {}
        self.assertFalse(settings.get("enabled"))
        self.assertFalse(settings.get("infinite"))
        self.assertEqual(settings.get("configuredLimit"), 0)
        self.assertEqual(settings.get("maxTasks"), EXTRA_TASKS_DEFAULT_MAX_TASKS)

    def test_personal_infinite_uses_default_max_tasks_for_ui(self):
        self._set_personal_context()

        resp = self.client.post(
            self.update_url,
            data=json.dumps({"enabled": True, "infinite": True, "maxTasks": 5}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("max_extra_tasks"), -1)

        settings = payload.get("extra_tasks") or {}
        self.assertTrue(settings.get("enabled"))
        self.assertTrue(settings.get("infinite"))
        self.assertEqual(settings.get("configuredLimit"), -1)
        self.assertEqual(settings.get("maxTasks"), EXTRA_TASKS_DEFAULT_MAX_TASKS)

    def test_org_member_cannot_update(self):
        self._set_org_context()
        self.membership.role = OrganizationMembership.OrgRole.MEMBER
        self.membership.save(update_fields=["role"])

        resp = self.client.post(
            self.update_url,
            data=json.dumps({"enabled": True, "infinite": False, "maxTasks": 10}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        payload = resp.json()
        self.assertFalse(payload.get("success"))

    def test_get_billing_settings_includes_extra_tasks_object(self):
        self._set_personal_context()

        resp = self.client.get(self.load_url)

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("max_extra_tasks", payload)
        self.assertIn("extra_tasks", payload)

        settings = payload.get("extra_tasks") or {}
        self.assertIn("enabled", settings)
        self.assertIn("infinite", settings)
        self.assertIn("maxTasks", settings)
        self.assertIn("configuredLimit", settings)
        self.assertIn("canModify", settings)
        self.assertIn("endpoints", settings)

