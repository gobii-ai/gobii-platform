import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.system_skills.defaults import HUBSPOT_NATIVE_SYSTEM_SKILL_KEY
from api.agent.system_skills.registry import SystemSkillDefinition
from api.agent.system_skills.service import enable_system_skills
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

    @patch("api.agent.tools.tool_manager.enable_tools")
    @patch("api.agent.system_skills.service._static_system_skill_tool_names", return_value=set())
    @patch("api.agent.system_skills.service.get_available_system_skill_tool_names", return_value={"eager_a", "eager_b"})
    def test_enabling_skill_protects_full_eager_surface_from_lru_eviction(
        self,
        _available_tool_names,
        _static_tool_names,
        enable_tools_mock,
    ):
        definition = SystemSkillDefinition(
            skill_key="test_eager_surface",
            name="Test eager surface",
            search_summary="Test",
            tool_names=("eager_a", "eager_b"),
            eager_tool_names=("eager_a", "eager_b"),
        )
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="eager_a",
        )
        enable_tools_mock.return_value = {
            "status": "success",
            "enabled": ["eager_b"],
            "already_enabled": ["eager_a"],
            "evicted": ["unrelated_old_tool"],
            "invalid": [],
        }

        result = enable_system_skills(
            self.agent,
            [definition.skill_key],
            available_skills=[definition],
        )

        self.assertEqual(result["status"], "success")
        enable_tools_mock.assert_called_once_with(
            self.agent,
            ["eager_a", "eager_b"],
            include_hidden_builtin=True,
        )
