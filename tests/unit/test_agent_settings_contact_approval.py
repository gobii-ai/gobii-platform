from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from api.models import (
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    PersistentAgent,
)


@tag("batch_console_allowlist")
class AgentSettingsContactApprovalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="contact-settings-owner",
            email="contact-settings-owner@example.com",
            password="pw",
            is_staff=True,
        )
        self.other = User.objects.create_user(
            username="contact-settings-other",
            email="contact-settings-other@example.com",
            password="pw",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.owner, name="Contact Settings Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.owner,
            name="Contact Settings Agent",
            charter="Manage contact approval settings.",
            browser_use_agent=browser_agent,
        )
        self.url = reverse("console_agent_settings", kwargs={"agent_id": self.agent.id})

    def _settings_form(self, **overrides):
        data = {
            "name": self.agent.name,
            "charter": self.agent.charter,
            "mini_description_mode": self.agent.mini_description_mode,
            "mini_description": self.agent.mini_description,
            "is_active": "on",
            "whitelist_policy": self.agent.whitelist_policy,
            "contact_approval_mode": self.agent.contact_approval_mode,
            "preferred_llm_tier": self.agent.preferred_llm_tier.key,
        }
        data.update(overrides)
        return data

    def test_settings_payload_defaults_to_required_approval(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertEqual(response.json()["agent"]["contactApprovalMode"], "require_approval")

    def test_settings_update_changes_mode_without_resolving_pending_requests(self):
        pending = CommsAllowlistRequest.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="pending@example.com",
            reason="Pending before settings change.",
            purpose="Existing request",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            self.url,
            self._settings_form(contact_approval_mode="auto_approve_email"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200, response.content.decode())
        self.agent.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(self.agent.contact_approval_mode, "auto_approve_email")
        self.assertEqual(pending.status, CommsAllowlistRequest.RequestStatus.PENDING)
        self.assertEqual(response.json()["contactApprovalMode"], "auto_approve_email")

    def test_settings_update_rejects_invalid_mode(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            self.url,
            self._settings_form(contact_approval_mode="allow_everything"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400, response.content.decode())
        self.assertIn("valid contact approval", response.json()["error"])
        self.agent.refresh_from_db()
        self.assertEqual(
            self.agent.contact_approval_mode,
            PersistentAgent.ContactApprovalMode.REQUIRE_APPROVAL,
        )

    def test_non_owner_cannot_manage_contact_approval_mode(self):
        self.client.force_login(self.other)

        response = self.client.post(
            self.url,
            self._settings_form(contact_approval_mode="auto_approve_email"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertIn(response.status_code, {403, 404})
        self.agent.refresh_from_db()
        self.assertEqual(
            self.agent.contact_approval_mode,
            PersistentAgent.ContactApprovalMode.REQUIRE_APPROVAL,
        )

    @patch("console.agent_settings.service.process_agent_events_task.delay")
    def test_allowlist_ajax_returns_structured_payload_without_legacy_html(self, _mock_process_events):
        self.client.force_login(self.owner)

        response = self.client.post(
            self.url,
            {
                "action": "add_allowlist",
                "channel": CommsChannel.EMAIL,
                "address": "structured@example.com",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200, response.content.decode())
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertIn("allowlist", payload)
        self.assertNotIn("html", payload)

    @patch("console.agent_settings.service.process_agent_events_task.delay")
    def test_allowlist_ajax_updates_contact_directions(self, _mock_process_events):
        entry = CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="permissions@example.com",
            allow_inbound=True,
            allow_outbound=True,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            self.url,
            {
                "action": "update_allowlist",
                "entry_id": str(entry.id),
                "allow_inbound": "false",
                "allow_outbound": "true",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200, response.content.decode())
        entry.refresh_from_db()
        self.assertFalse(entry.allow_inbound)
        self.assertTrue(entry.allow_outbound)
        serialized = next(
            item for item in response.json()["allowlist"]["entries"]
            if item["id"] == str(entry.id)
        )
        self.assertFalse(serialized["allowInbound"])
        self.assertTrue(serialized["allowOutbound"])
