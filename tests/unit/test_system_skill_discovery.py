import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from agents.pretrained_worker_definitions import TEMPLATE_DEFINITIONS
from api.agent.core.prompt_context import build_prompt_context_preview
from api.agent.system_skills.defaults import RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY
from api.agent.system_skills.discovery import (
    format_system_skill_discovery_prompt,
    get_system_skill_discovery_suggestions,
    matching_system_skill_definitions,
)
from api.agent.system_skills.registry import SYSTEM_SKILL_REGISTRY, SystemSkillDefinition
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentEnabledTool,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
)
from tests.utils.llm_seed import seed_persistent_basic


User = get_user_model()


@tag("batch_mcp_tools")
class SystemSkillDiscoveryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        seed_persistent_basic()

    def setUp(self):
        suffix = uuid.uuid4().hex
        self.user = User.objects.create_user(
            username=f"skill-discovery-{suffix}@example.com",
            email=f"skill-discovery-{suffix}@example.com",
            password="password",
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name=f"skill-discovery-browser-{suffix}",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Recruiting Agent",
            charter="General research assistant",
            browser_use_agent=browser_agent,
        )
        self.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel="email",
            address=f"agent-{suffix}@example.com",
            is_primary=True,
        )
        self.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel="email",
            address=f"user-{suffix}@example.com",
        )

    def _inbound(self, body: str) -> PersistentAgentMessage:
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=self.user_endpoint,
            to_endpoint=self.agent_endpoint,
            body=body,
        )

    def test_matching_uses_high_confidence_template_and_custom_phrases(self):
        talent_scout = next(template for template in TEMPLATE_DEFINITIONS if template.code == "talent-scout")
        examples = (
            f"{talent_scout.display_name}\n{talent_scout.charter}",
            "Candidate Sourcing template: build recruiter-reviewed shortlists for open roles.",
            "You are a recruiting sourcing agent that finds qualified candidate prospects.",
            "Source & screen candidates for this open role.",
            "Please source candidates for the engineering team.",
        )

        for text in examples:
            with self.subTest(text=text):
                matches = matching_system_skill_definitions(text)
                self.assertIn(
                    RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,
                    {definition.skill_key for definition in matches},
                )

    def test_matching_normalizes_case_punctuation_and_spacing(self):
        matches = matching_system_skill_definitions("CANDIDATE---SOURCING for a new role")

        self.assertIn(
            RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,
            {definition.skill_key for definition in matches},
        )

    def test_unrelated_research_does_not_match(self):
        matches = matching_system_skill_definitions(
            "Research the candidates in the upcoming municipal election and summarize their platforms."
        )

        self.assertNotIn(
            RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,
            {definition.skill_key for definition in matches},
        )

    def test_suggestion_uses_charter(self):
        self.agent.charter = "You are a Talent Scout for technical hiring."
        self.agent.save(update_fields=["charter"])

        suggestions = get_system_skill_discovery_suggestions(self.agent)

        self.assertEqual(
            [suggestion.skill_key for suggestion in suggestions],
            [RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY],
        )
        self.assertEqual(suggestions[0].search_query, "recruitment sourcing")

    def test_suggestion_uses_only_three_latest_inbound_messages(self):
        self._inbound("Please source candidates for this role.")
        for body in ("first unrelated follow-up", "second unrelated follow-up", "third unrelated follow-up"):
            self._inbound(body)

        self.assertEqual(get_system_skill_discovery_suggestions(self.agent), [])

        self._inbound("Please shortlist candidates for the revised role.")

        self.assertEqual(
            [suggestion.skill_key for suggestion in get_system_skill_discovery_suggestions(self.agent)],
            [RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY],
        )

    @patch("api.agent.system_skills.discovery.get_available_system_skill_tool_names", return_value=set())
    def test_suggestions_are_capped_at_two(self, _mock_available_tools):
        definitions = {
            f"test_discovery_{index}": SystemSkillDefinition(
                skill_key=f"test_discovery_{index}",
                name=f"Test Discovery {index}",
                search_summary="Test-only discovery skill.",
                tool_names=(),
                discovery_triggers=("specialized workflow",),
                discovery_query=f"test discovery {index}",
            )
            for index in range(3)
        }
        self._inbound("Run this specialized workflow.")

        with patch.dict(SYSTEM_SKILL_REGISTRY, definitions):
            suggestions = get_system_skill_discovery_suggestions(self.agent)

        self.assertEqual(len(suggestions), 2)

    def test_enabled_skill_is_not_suggested(self):
        self._inbound("Source candidates for this role.")
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,
            is_enabled=True,
        )

        self.assertEqual(get_system_skill_discovery_suggestions(self.agent), [])
        self.assertEqual(format_system_skill_discovery_prompt(self.agent), ("", ()))

    @patch("api.agent.system_skills.discovery.get_available_system_skill_tool_names", return_value=set())
    def test_unavailable_skill_is_not_suggested(self, _mock_available_tools):
        self._inbound("Source candidates for this role.")

        self.assertEqual(get_system_skill_discovery_suggestions(self.agent), [])

    def test_matching_search_tools_attempt_suppresses_current_request_hint(self):
        inbound = self._inbound("Source candidates for this role.")
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Discover sourcing guidance")
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="search_tools",
            tool_params={"query": "recruitment sourcing"},
            result='{"status":"success"}',
        )

        self.assertGreaterEqual(step.created_at, inbound.timestamp)
        self.assertEqual(get_system_skill_discovery_suggestions(self.agent), [])

    def test_unrelated_search_tools_attempt_does_not_suppress_hint(self):
        self._inbound("Source candidates for this role.")
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Discover spreadsheet tools")
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="search_tools",
            tool_params={"query": "spreadsheet reporting"},
            result='{"status":"success"}',
        )

        suggestions = get_system_skill_discovery_suggestions(self.agent)

        self.assertEqual(
            [suggestion.skill_key for suggestion in suggestions],
            [RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY],
        )

    def test_prompt_hint_does_not_enable_skill(self):
        self._inbound("Source candidates for a product management role.")
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="mcp_brightdata_search_engine",
        )

        block, keys = format_system_skill_discovery_prompt(self.agent)

        self.assertEqual(keys, (RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,))
        self.assertIn("## Suggested Capability Discovery", block)
        self.assertIn('`search_tools("recruitment sourcing")`', block)
        self.assertIn("do not replace or expand it with task details", block)
        self.assertIn("before using candidate-search tools", block)
        self.assertIn("even when an enabled web, search, data, or integration tool", block)
        self.assertFalse(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,
            ).exists()
        )

    def test_rendered_system_prompt_includes_discovery_hint_and_rule_exception(self):
        self._inbound("Please source candidates for this role.")

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            messages, _tokens, _metadata = build_prompt_context_preview(self.agent)

        system_prompt = next(message["content"] for message in messages if message["role"] == "system")
        self.assertIn("## Suggested Capability Discovery", system_prompt)
        self.assertIn("unless a capability-discovery hint says a system skill may apply", system_prompt)
        self.assertFalse(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,
            ).exists()
        )
