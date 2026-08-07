import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.system_skills.defaults import HUBSPOT_NATIVE_SYSTEM_SKILL_KEY, WEBHOOKS_SYSTEM_SKILL_KEY
from api.agent.system_skills.registry import get_system_skill_definition, shortlist_system_skills
from api.agent.system_skills.service import (
    enable_system_skills,
    get_available_system_skill_tool_names,
)
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentEnabledTool, PersistentAgentSystemSkillState
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

    def test_webhook_skill_is_discoverable_for_explicit_pipedream_request(self):
        shortlisted_skills = shortlist_system_skills(
            "Pipedream",
            available_tool_names={
                "manage_inbound_webhooks",
                "manage_outbound_webhooks",
                "send_webhook_event",
            },
        )

        self.assertIn(WEBHOOKS_SYSTEM_SKILL_KEY, [skill.skill_key for skill in shortlisted_skills])

    def test_secure_delegation_shortlists_meta_gobii_dependency(self):
        secure = get_system_skill_definition("secure_credential_delegation")
        meta = get_system_skill_definition("meta_gobii")

        shortlisted_skills = shortlist_system_skills(
            "secure credential delegation",
            available_tool_names={*secure.tool_names, *meta.tool_names},
        )

        self.assertIn(secure.skill_key, [skill.skill_key for skill in shortlisted_skills])
        self.assertIn(meta.skill_key, [skill.skill_key for skill in shortlisted_skills])

    def test_enabling_secure_delegation_enables_meta_gobii_dependency(self):
        User = get_user_model()
        user = User.objects.create_user(username=f"secure-dependency-{uuid.uuid4().hex[:8]}")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Secure Dependency Browser")
        agent = PersistentAgent.objects.create(
            user=user,
            name="Secure Dependency Agent",
            charter="Provision credentials for child Gobiis.",
            browser_use_agent=browser_agent,
        )
        secure = get_system_skill_definition("secure_credential_delegation")
        meta = get_system_skill_definition("meta_gobii")

        result = enable_system_skills(agent, [secure.skill_key], available_skills=[secure, meta])

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            set(result["enabled"]),
            {secure.skill_key, meta.skill_key},
        )
        self.assertEqual(
            set(
                PersistentAgentSystemSkillState.objects.filter(agent=agent, is_enabled=True).values_list(
                    "skill_key", flat=True
                )
            ),
            {secure.skill_key, meta.skill_key},
        )
