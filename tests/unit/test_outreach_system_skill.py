import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.tools.sqlite_skills import format_recent_skills_for_prompt
from api.agent.system_skills.defaults import (
    OUTREACH_SYSTEM_SKILL_KEY,
    RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,
)
from api.agent.system_skills.discovery import get_system_skill_discovery_suggestions
from api.agent.system_skills.registry import SYSTEM_SKILL_REGISTRY, shortlist_system_skills
from api.agent.system_skills.service import default_enabled_system_skill_keys, enable_system_skills
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentSystemSkillState
from tests.utils.llm_seed import seed_persistent_basic


User = get_user_model()


@tag("batch_mcp_tools")
class OutreachSystemSkillTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        seed_persistent_basic()

    def setUp(self):
        suffix = uuid.uuid4().hex
        self.user = User.objects.create_user(
            username=f"outreach-skill-{suffix}@example.com",
            email=f"outreach-skill-{suffix}@example.com",
            password="password",
        )
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name=f"outreach-browser-{suffix}",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Outreach Agent",
            charter="General business assistant",
            browser_use_agent=browser_agent,
        )

    @staticmethod
    def _discovery_matches(text: str):
        return shortlist_system_skills(
            text,
            available_tool_names={"search_tools", "send_email"},
            limit=len(SYSTEM_SKILL_REGISTRY),
            discovery_only=True,
        )

    def test_outreach_definition_is_registered_and_not_default_enabled(self):
        definition = SYSTEM_SKILL_REGISTRY[OUTREACH_SYSTEM_SKILL_KEY]

        self.assertEqual(definition.name, "Outreach")
        self.assertEqual(definition.tool_names, ("send_email",))
        self.assertFalse(definition.default_enabled)
        self.assertNotIn(OUTREACH_SYSTEM_SKILL_KEY, default_enabled_system_skill_keys())
        self.assertIn("User-provided instructions and approved copy control", definition.prompt_instructions)
        self.assertIn("Default to valid, restrained body-only HTML", definition.prompt_instructions)
        self.assertIn("Never leave unresolved placeholders", definition.prompt_instructions)
        self.assertIn("These are defaults, not bans", definition.prompt_instructions)
        self.assertIn("Never replace intentional em dashes", definition.prompt_instructions)
        self.assertIn("Immediately before every `send_email` call", definition.prompt_instructions)
        self.assertIn("reply_to_message_id", definition.prompt_instructions)
        self.assertIn("opted_out", definition.prompt_instructions)

    def test_outreach_style_defaults_do_not_override_explicit_user_direction(self):
        instructions = SYSTEM_SKILL_REGISTRY[OUTREACH_SYSTEM_SKILL_KEY].prompt_instructions

        self.assertIn("When asked to preserve or send supplied copy exactly, do not rewrite it", instructions)
        self.assertIn("Keep intentional branding, structure, punctuation, emoji, length, and multiple asks", instructions)
        self.assertNotIn("Do not use em dashes", instructions)
        self.assertNotIn("em dash character `—` must appear zero times", instructions)

    def test_enable_outreach_uses_static_send_email_tool(self):
        result = enable_system_skills(self.agent, [OUTREACH_SYSTEM_SKILL_KEY])

        self.assertEqual(result["enabled"], [OUTREACH_SYSTEM_SKILL_KEY])
        self.assertEqual(result["invalid"], [])
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=OUTREACH_SYSTEM_SKILL_KEY,
                is_enabled=True,
            ).exists()
        )

    def test_enabled_outreach_renders_in_the_agent_prompt(self):
        enable_system_skills(self.agent, [OUTREACH_SYSTEM_SKILL_KEY])
        PersistentAgentSystemSkillState.objects.filter(
            agent=self.agent,
            skill_key=OUTREACH_SYSTEM_SKILL_KEY,
        ).update(last_used_at=timezone.now())

        block = format_recent_skills_for_prompt(self.agent, limit=1)

        self.assertIn("System Skill: Outreach", block)
        self.assertIn("Tools: send_email", block)
        self.assertIn("Treat outreach as a recipient-facing workflow", block)
        self.assertIn("User-provided instructions and approved copy control", block)

    def test_discovery_suggests_outreach_from_charter(self):
        self.agent.charter = "Write and send personalized cold email outreach to approved prospects."
        self.agent.save(update_fields=["charter"])

        suggestions = get_system_skill_discovery_suggestions(self.agent)

        self.assertIn(
            OUTREACH_SYSTEM_SKILL_KEY,
            {suggestion.skill_key for suggestion in suggestions},
        )

    def test_pure_candidate_sourcing_does_not_select_outreach(self):
        matches = self._discovery_matches("Find candidates for a backend engineering role.")
        skill_keys = {definition.skill_key for definition in matches}

        self.assertIn(RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY, skill_keys)
        self.assertNotIn(OUTREACH_SYSTEM_SKILL_KEY, skill_keys)

    def test_lead_list_research_does_not_select_outreach(self):
        matches = self._discovery_matches("Build a lead list of manufacturing companies in Ohio.")

        self.assertNotIn(
            OUTREACH_SYSTEM_SKILL_KEY,
            {definition.skill_key for definition in matches},
        )

    def test_combined_candidate_request_selects_sourcing_and_outreach(self):
        matches = self._discovery_matches("Find and contact candidates for this role.")
        skill_keys = {definition.skill_key for definition in matches}

        self.assertIn(RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY, skill_keys)
        self.assertIn(OUTREACH_SYSTEM_SKILL_KEY, skill_keys)

    def test_explicit_outreach_phrases_select_outreach(self):
        for prompt in (
            "Send a cold email to this prospect.",
            "Create a candidate outreach sequence.",
            "Send customer success outreach after the service issue.",
            "Draft support outreach to the affected account.",
            "Write a partnership outreach email.",
            "Prepare PR outreach for this announcement.",
            "Follow up email after no reply.",
        ):
            with self.subTest(prompt=prompt):
                matches = self._discovery_matches(prompt)
                self.assertIn(
                    OUTREACH_SYSTEM_SKILL_KEY,
                    {definition.skill_key for definition in matches},
                )
