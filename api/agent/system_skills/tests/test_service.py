import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.system_skills.defaults import HUBSPOT_NATIVE_SYSTEM_SKILL_KEY, WEBHOOKS_SYSTEM_SKILL_KEY
from api.agent.system_skills.registry import shortlist_system_skills
from api.agent.system_skills.service import (
    enable_system_skills,
    get_available_system_skill_tool_names,
)
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentEnabledTool
from api.services.pipedream_apps import PIPEDREAM_RUNTIME_NAME


@tag("batch_mcp_tools")
class NativeSystemSkillPipedreamCleanupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username=f"native-skill-{uuid.uuid4().hex[:8]}")
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Native Cleanup Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Native Cleanup Agent",
            charter="Test agent.",
            browser_use_agent=browser_agent,
        )

    def _enabled_tool_names(self) -> set[str]:
        return set(
            PersistentAgentEnabledTool.objects.filter(agent=self.agent).values_list("tool_full_name", flat=True)
        )

    def test_enabling_native_skill_does_not_remove_overlapping_pipedream_tools_before_connection(self):
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="http_request",
            tool_name="http_request",
        )
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="hubspot-search-crm-objects",
            tool_name="hubspot-search-crm-objects",
            tool_server=PIPEDREAM_RUNTIME_NAME,
        )

        result = enable_system_skills(self.agent, [HUBSPOT_NATIVE_SYSTEM_SKILL_KEY])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["already_enabled"], [])
        self.assertEqual(
            self._enabled_tool_names(),
            {"http_request", "hubspot-search-crm-objects"},
        )


@tag("batch_mcp_tools")
class PlanningSystemSkillDiscoveryTests(TestCase):
    def test_webhook_skill_is_discoverable_during_planning(self):
        User = get_user_model()
        user = User.objects.create_user(username=f"webhook-discovery-{uuid.uuid4().hex[:8]}")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Webhook Discovery Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Webhook Discovery Agent",
            charter="Create an inbound webhook.",
            browser_use_agent=browser_agent,
            planning_state=PersistentAgent.PlanningState.PLANNING,
        )

        available_tool_names = get_available_system_skill_tool_names(agent)
        shortlisted_skills = shortlist_system_skills(
            "inbound webhook",
            available_tool_names=available_tool_names,
        )

        self.assertIn("manage_inbound_webhooks", available_tool_names)
        self.assertIn("manage_outbound_webhooks", available_tool_names)
        self.assertIn("send_webhook_event", available_tool_names)
        self.assertIn(WEBHOOKS_SYSTEM_SKILL_KEY, [skill.skill_key for skill in shortlisted_skills])
