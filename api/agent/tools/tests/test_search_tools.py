from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag
from django.utils import timezone

from api.agent.core.prompt_context import get_agent_tools
from api.agent.system_skills.defaults import APOLLO_NATIVE_SYSTEM_SKILL
from api.agent.tools.meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY
from api.agent.tools.search_tools import (
    _directly_named_system_skill_keys,
    is_persistent_team_request,
    _preserve_meta_gobii_team_intent,
    _route_meta_gobii_team_search,
    search_tools,
)
from api.agent.tools.sqlite_skills import format_recent_skills_for_prompt
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentSystemSkillState,
)


def _tool_names(tools):
    return {
        tool["function"]["name"]
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and isinstance(tool["function"].get("name"), str)
    }


@tag("batch_mcp_tools")
class SearchToolsSystemSkillTests(SimpleTestCase):
    def test_team_creation_preserves_meta_gobii_intent(self):
        for request in ("Create an entire research team", "Build an analyst team", "Deploy a scout team", "Launch a specialist team"):
            self.assertEqual(_route_meta_gobii_team_search(request, "web research"), "meta gobii control plane")
        self.assertEqual(_route_meta_gobii_team_search("Do not create a research team", "web research"), "web research")

    def test_team_request_detection_excludes_ordinary_research(self):
        self.assertTrue(is_persistent_team_request("Prototype a team of specialist agents to track NYC events."))
        self.assertFalse(is_persistent_team_request("Research NYC events and recommend a team outing."))

    def test_team_request_detection_excludes_reports_about_existing_teams(self):
        for request in (
            "Build a report comparing our team of analysts with peers.",
            "Create a dashboard for our research team of analysts.",
            "Configure a dashboard showing our specialist team's performance.",
            "Maintain a report for our analyst team.",
            "Build a team dashboard.",
            "Create a team report.",
            "Build with our existing team of agents.",
            "Build alongside our research team.",
            "Build a platform that coordinates a team of agents.",
            "Create a dashboard containing our analyst team.",
        ):
            with self.subTest(request=request):
                self.assertFalse(is_persistent_team_request(request))

    def test_team_request_detection_includes_explicit_persistent_team_work(self):
        for request in (
            "Build a persistent multi-agent team of research analysts.",
            "Build a market research team of analysts.",
            "Build a sales team of agents.",
            "Build a data analysis team of agents.",
            "I can't wait to build a market research team of agents.",
            "Configure our existing team of specialist agents to share briefings.",
            "Maintain the dedicated scout team of agents.",
            "Set up an autonomous analyst team to monitor competitors.",
        ):
            with self.subTest(request=request):
                self.assertTrue(is_persistent_team_request(request))

    def test_team_request_detection_rejects_clause_level_negation(self):
        for request in (
            "I do not want you to build a persistent team of agents.",
            "Please don't configure our sales team of agents.",
            "Never maintain an autonomous research team.",
            "I cannot build a team of agents; what should I do?",
            "I can't build a team of agents; what should I do?",
            "We shouldn't build a research team.",
            "We won't build a research team.",
        ):
            with self.subTest(request=request):
                self.assertFalse(is_persistent_team_request(request))

    @patch("api.agent.tools.search_tools.get_enabled_system_skill_states")
    def test_latest_inbound_repairs_lossy_team_search_until_meta_is_enabled(self, enabled_states):
        agent = SimpleNamespace(pk=1, agent_messages=MagicMock())
        agent.agent_messages.filter.return_value.order_by.return_value.values_list.return_value.first.return_value = "Create an entire research team"
        enabled_states.return_value.filter.return_value.exists.return_value = False
        self.assertEqual(_preserve_meta_gobii_team_intent(agent, "web research"), "meta gobii control plane")
        enabled_states.return_value.filter.return_value.exists.return_value = True
        self.assertEqual(_preserve_meta_gobii_team_intent(agent, "web research"), "web research")

    def test_explicit_system_skill_name_is_detected(self):
        self.assertEqual(
            _directly_named_system_skill_keys(
                "Search Apollo for RevOps leaders",
                [APOLLO_NATIVE_SYSTEM_SKILL],
            ),
            ["apollo_native"],
        )

    @patch("api.agent.tools.search_tools._search_with_llm")
    @patch("api.agent.tools.search_tools.enable_system_skills")
    @patch("api.agent.tools.search_tools.get_available_system_skill_tool_names", return_value={"http_request"})
    @patch("api.agent.tools.search_tools.shortlist_system_skills", return_value=[APOLLO_NATIVE_SYSTEM_SKILL])
    @patch("api.agent.tools.search_tools.get_latest_skill_versions", return_value=[])
    @patch("api.agent.tools.search_tools.get_compatible_global_skills", return_value=[])
    @patch("api.agent.tools.search_tools.get_available_eval_synthetic_tool_catalog", return_value=[])
    @patch("api.agent.tools.search_tools.get_available_custom_tool_entries", return_value={})
    @patch("api.agent.tools.search_tools.get_available_builtin_tool_entries", return_value={})
    @patch("api.agent.tools.search_tools.get_agent_tool_blacklist", return_value=set())
    @patch("api.agent.tools.search_tools.is_eval_agent", return_value=True)
    @patch("api.agent.tools.search_tools.filter_deprecated_pipedream_tools_for_agent", return_value=[])
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    def test_explicit_native_provider_bypasses_legacy_tool_selection(
        self,
        get_mcp_manager,
        _filter_pipedream,
        _is_eval_agent,
        _get_blacklist,
        _get_builtin,
        _get_custom,
        _get_synthetic,
        _get_global_skills,
        _get_agent_skills,
        _shortlist,
        _available_system_tools,
        enable_system_skills,
        search_with_llm,
    ):
        get_mcp_manager.return_value = SimpleNamespace(
            _initialized=True,
            get_tools_for_agent=lambda _agent: [],
        )
        enable_system_skills.return_value = {
            "status": "success",
            "enabled": ["apollo_native"],
            "already_enabled": [],
            "invalid": [],
            "evicted": [],
            "pipedream_apps": {},
        }
        agent = SimpleNamespace(id=1, organization=None, user=None)

        result = search_tools(agent, "Search Apollo for VP Sales contacts")

        self.assertEqual(result["system_skills"]["enabled"], ["apollo_native"])
        enable_system_skills.assert_called_once_with(
            agent,
            ["apollo_native"],
            available_skills=[APOLLO_NATIVE_SYSTEM_SKILL],
        )
        search_with_llm.assert_not_called()


@tag("batch_mcp_tools")
class SearchToolsRediscoveryPathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="search-tools-rediscovery",
            email="search-tools-rediscovery@example.com",
            password="secret",
        )
        browser = BrowserUseAgent.objects.create(user=user, name="Rediscovery Browser")
        cls.agent = PersistentAgent.objects.create(
            user=user,
            name="Rediscovery Agent",
            charter="Coordinate specialist Gobiis.",
            browser_use_agent=browser,
        )

    @patch("api.agent.tools.search_tools._search_with_llm")
    @patch("api.agent.tools.tool_manager.get_mcp_manager")
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    def test_exact_hidden_operation_first_loads_its_system_skill_guardrails(
        self,
        search_manager,
        tool_manager,
        search_with_llm,
    ):
        manager = MagicMock()
        manager._initialized = True
        manager.get_tools_for_agent.return_value = []
        manager.get_enabled_tools_definitions.return_value = []
        manager.is_tool_blacklisted.return_value = False
        search_manager.return_value = manager
        tool_manager.return_value = manager

        result = search_tools(self.agent, "meta_gobii_create_agent")

        self.assertEqual(result["system_skills"]["enabled"], [META_GOBII_SYSTEM_SKILL_KEY])
        names = _tool_names(get_agent_tools(self.agent))
        self.assertIn("meta_gobii_list_agents", names)
        self.assertNotIn("meta_gobii_create_agent", names)
        search_with_llm.assert_not_called()

    @patch("api.agent.tools.search_tools._search_with_llm")
    @patch("api.agent.tools.tool_manager.get_mcp_manager")
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    def test_omitted_meta_skill_rediscovery_can_load_exact_lazy_operation(
        self,
        search_manager,
        tool_manager,
        search_with_llm,
    ):
        manager = MagicMock()
        manager._initialized = True
        manager.get_tools_for_agent.return_value = []
        manager.get_enabled_tools_definitions.return_value = []
        manager.is_tool_blacklisted.return_value = False
        search_manager.return_value = manager
        tool_manager.return_value = manager

        initial = search_tools(self.agent, "Meta Gobii")
        self.assertEqual(initial["system_skills"]["enabled"], [META_GOBII_SYSTEM_SKILL_KEY])

        now = timezone.now()
        PersistentAgentSystemSkillState.objects.filter(
            agent=self.agent,
            skill_key=META_GOBII_SYSTEM_SKILL_KEY,
        ).update(last_used_at=now - timedelta(hours=2))
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=APOLLO_NATIVE_SYSTEM_SKILL.skill_key,
            is_enabled=True,
            last_used_at=now - timedelta(hours=1),
        )
        before = format_recent_skills_for_prompt(self.agent, limit=1)
        self.assertIn("System Skill: Apollo\n", before)
        self.assertIn("System Skill: Meta Gobii (meta_gobii)", before)

        rediscovered = search_tools(self.agent, "Meta Gobii")
        self.assertEqual(
            rediscovered["system_skills"]["already_enabled"],
            [META_GOBII_SYSTEM_SKILL_KEY],
        )
        after = format_recent_skills_for_prompt(self.agent, limit=1)
        self.assertIn("System Skill: Meta Gobii\n", after)
        self.assertIn("System Skill: Apollo (apollo_native)", after)

        before_tools = _tool_names(get_agent_tools(self.agent))
        self.assertNotIn("meta_gobii_create_agent", before_tools)

        loaded = search_tools(self.agent, "Load meta_gobii_create_agent")
        self.assertEqual(loaded["tools"]["enabled"], ["meta_gobii_create_agent"])
        self.assertIn("meta_gobii_create_agent", _tool_names(get_agent_tools(self.agent)))
        search_with_llm.assert_not_called()

    @patch("api.agent.tools.search_tools._search_with_llm")
    @patch("api.agent.tools.tool_manager.get_mcp_manager")
    @patch("api.agent.tools.search_tools.get_mcp_manager")
    def test_exact_lazy_operation_refreshes_omitted_system_skill(
        self,
        search_manager,
        tool_manager,
        search_with_llm,
    ):
        manager = MagicMock()
        manager._initialized = True
        manager.get_tools_for_agent.return_value = []
        manager.get_enabled_tools_definitions.return_value = []
        manager.is_tool_blacklisted.return_value = False
        search_manager.return_value = manager
        tool_manager.return_value = manager

        search_tools(self.agent, "Meta Gobii")
        now = timezone.now()
        PersistentAgentSystemSkillState.objects.filter(
            agent=self.agent,
            skill_key=META_GOBII_SYSTEM_SKILL_KEY,
        ).update(last_used_at=now - timedelta(hours=2))
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=APOLLO_NATIVE_SYSTEM_SKILL.skill_key,
            is_enabled=True,
            last_used_at=now - timedelta(hours=1),
        )
        self.assertIn("System Skill: Apollo\n", format_recent_skills_for_prompt(self.agent, limit=1))

        loaded = search_tools(self.agent, "Load meta_gobii_create_agent")

        self.assertEqual(loaded["tools"]["enabled"], ["meta_gobii_create_agent"])
        refreshed = format_recent_skills_for_prompt(self.agent, limit=1)
        self.assertIn("System Skill: Meta Gobii\n", refreshed)
        search_with_llm.assert_not_called()
