import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.system_skills.defaults import (
    APOLLO_NATIVE_SYSTEM_SKILL_KEY,
    GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
    HUBSPOT_NATIVE_SYSTEM_SKILL_KEY,
)
from api.agent.system_skills.service import enable_system_skills
from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentSystemSkillState,
)
from api.services.pipedream_apps import PIPEDREAM_RUNTIME_NAME


@tag("batch_mcp_tools")
class NativeSystemSkillPipedreamCleanupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username=f"native-skill-{uuid.uuid4().hex[:8]}")
        self.agent = self._create_agent(self.user, "Native Cleanup Agent")
        self.other_agent = self._create_agent(self.user, "Other Native Cleanup Agent")
        self.pipedream_server, _ = MCPServerConfig.objects.get_or_create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name=PIPEDREAM_RUNTIME_NAME,
            defaults={
                "display_name": "Pipedream",
                "description": "",
                "url": "https://example.com/mcp",
            },
        )
        self._enable_http_request(self.agent)
        self._enable_http_request(self.other_agent)

    def _create_agent(self, user, name: str) -> PersistentAgent:
        browser_agent = BrowserUseAgent.objects.create(user=user, name=f"{name} Browser")
        return PersistentAgent.objects.create(
            user=user,
            name=name,
            charter="Test agent.",
            browser_use_agent=browser_agent,
        )

    def _enable_http_request(self, agent: PersistentAgent) -> None:
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="http_request",
            tool_name="http_request",
        )

    def _enable_pipedream_tool(
        self,
        agent: PersistentAgent,
        tool_name: str,
        *,
        via_server_config: bool = False,
    ) -> None:
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name=tool_name,
            tool_name=tool_name,
            tool_server="" if via_server_config else PIPEDREAM_RUNTIME_NAME,
            server_config=self.pipedream_server if via_server_config else None,
        )

    def _enabled_tool_names(self, agent: PersistentAgent) -> set[str]:
        return set(
            PersistentAgentEnabledTool.objects.filter(agent=agent).values_list("tool_full_name", flat=True)
        )

    def test_enabling_hubspot_native_removes_overlapping_pipedream_tools_only(self):
        self._enable_pipedream_tool(self.agent, "hubspot-search-crm-objects")
        self._enable_pipedream_tool(self.agent, "hubspot-create-contact", via_server_config=True)
        self._enable_pipedream_tool(self.agent, "slack-post-message")
        self._enable_pipedream_tool(self.other_agent, "hubspot-search-crm-objects")
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="hubspot-local-helper",
            tool_name="hubspot-local-helper",
        )

        result = enable_system_skills(self.agent, [HUBSPOT_NATIVE_SYSTEM_SKILL_KEY])

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            set(result["disabled_pipedream_tools"]),
            {"hubspot-create-contact", "hubspot-search-crm-objects"},
        )
        self.assertEqual(
            self._enabled_tool_names(self.agent),
            {"http_request", "slack-post-message", "hubspot-local-helper"},
        )
        self.assertIn("hubspot-search-crm-objects", self._enabled_tool_names(self.other_agent))

    def test_native_skill_mappings_remove_matching_pipedream_app_tools(self):
        cases = (
            (
                GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
                {
                    "google_sheets-add-row",
                    "google_drive-upload-file",
                },
                {"google_docs-create-document"},
            ),
            (
                APOLLO_NATIVE_SYSTEM_SKILL_KEY,
                {
                    "apollo_io-search-contacts",
                    "apollo_io_oauth-search-contacts",
                },
                set(),
            ),
        )
        for skill_key, pipedream_tool_names, preserved_tool_names in cases:
            with self.subTest(skill_key=skill_key):
                agent = self._create_agent(self.user, f"{skill_key} Agent")
                self._enable_http_request(agent)
                for pipedream_tool_name in pipedream_tool_names | preserved_tool_names:
                    self._enable_pipedream_tool(agent, pipedream_tool_name)
                self._enable_pipedream_tool(agent, "slack-post-message")

                result = enable_system_skills(agent, [skill_key])

                self.assertEqual(result["status"], "success")
                self.assertEqual(set(result["disabled_pipedream_tools"]), pipedream_tool_names)
                self.assertEqual(
                    self._enabled_tool_names(agent),
                    {"http_request", "slack-post-message"} | preserved_tool_names,
                )

    def test_reenabling_already_enabled_native_skill_still_cleans_up_pipedream_tools(self):
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=HUBSPOT_NATIVE_SYSTEM_SKILL_KEY,
            is_enabled=True,
        )
        self._enable_pipedream_tool(self.agent, "hubspot-search-crm-objects")

        result = enable_system_skills(self.agent, [HUBSPOT_NATIVE_SYSTEM_SKILL_KEY])

        self.assertEqual(result["already_enabled"], [HUBSPOT_NATIVE_SYSTEM_SKILL_KEY])
        self.assertEqual(result["disabled_pipedream_tools"], ["hubspot-search-crm-objects"])
        self.assertEqual(self._enabled_tool_names(self.agent), {"http_request"})

    def test_cleanup_runs_before_enabling_native_dependency_to_avoid_lru_eviction(self):
        agent = self._create_agent(self.user, "Native Tool Cap Agent")
        self._enable_pipedream_tool(agent, "hubspot-search-crm-objects")
        PersistentAgentEnabledTool.objects.create(
            agent=agent,
            tool_full_name="slack-post-message",
            tool_name="slack-post-message",
            tool_server=PIPEDREAM_RUNTIME_NAME,
        )

        with (
            patch("api.agent.tools.tool_manager.get_enabled_tool_limit", return_value=2),
            patch("api.agent.tools.tool_manager.MCPToolManager.get_tools_for_agent", return_value=[]),
        ):
            result = enable_system_skills(agent, [HUBSPOT_NATIVE_SYSTEM_SKILL_KEY])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["disabled_pipedream_tools"], ["hubspot-search-crm-objects"])
        self.assertEqual(self._enabled_tool_names(agent), {"http_request", "slack-post-message"})
        self.assertEqual(result["evicted"], [])
