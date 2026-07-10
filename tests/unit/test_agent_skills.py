import os
import sqlite3
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.tools.sqlite_skills import (
    apply_sqlite_skill_updates,
    format_recent_skills_for_prompt,
    refresh_skills_for_tool,
    seed_sqlite_skills,
)
from api.agent.system_skills.service import (
    default_enabled_system_skill_keys,
    enable_and_refresh_system_skills_for_tool,
    enable_system_skills,
)
from api.agent.system_skills.defaults import CODE_WORK_SYSTEM_SKILL_KEY, DEFAULT_SYSTEM_SKILL_DEFINITIONS, IMAGE_GENERATION_SYSTEM_SKILL
from api.agent.system_skills.image_generation import IMAGE_GENERATION_SYSTEM_SKILL_KEY
from api.agent.tools.custom_tool_names import CREATE_CUSTOM_TOOL_NAME, CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path
from api.agent.tools.tool_manager import (
    ToolCatalogEntry,
    ensure_skill_tools_enabled,
    get_available_tool_ids,
)
from api.models import (
    BrowserUseAgent,
    GlobalAgentSkill,
    GlobalSecret,
    PersistentAgent,
    PersistentAgentCustomTool,
    PersistentAgentEnabledTool,
    PersistentAgentSecret,
    PersistentAgentSkill,
    PersistentAgentSystemSkillState,
    UserQuota,
)
from util.analytics import AnalyticsEvent


@tag("batch_agent_tools")
class AgentSkillsPersistenceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="skills-tests@example.com",
            email="skills-tests@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 100
        quota.save(update_fields=["agent_limit"])

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="skills-browser-agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Skills Agent",
            charter="Track repeatable workflows",
            browser_use_agent=browser_agent,
        )

        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "state.db")
        self.token = set_sqlite_db_path(self.db_path)

    def tearDown(self):
        reset_sqlite_db_path(self.token)
        self.tmp.cleanup()

    @patch("util.analytics.Analytics.track_event")
    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch"})
    def test_sqlite_skill_create_emits_analytics(self, _mock_available_tools, mock_track_event):
        baseline = seed_sqlite_skills(self.agent)
        self.assertIsNotNone(baseline)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO "__agent_skills" (id, name, description, version, tools, instructions)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    "skill-create-1",
                    "daily-brief",
                    "Daily digest workflow",
                    1,
                    '["sqlite_batch"]',
                    "Collect updates and summarize.",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        result = apply_sqlite_skill_updates(self.agent, baseline)

        self.assertFalse(result.errors)
        self.assertTrue(result.changed)
        self.assertEqual(result.created_versions, ["daily-brief@1"])
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.PERSISTENT_AGENT_SKILL_CREATED)
        self.assertEqual(kwargs["user_id"], self.agent.user_id)
        self.assertEqual(kwargs["properties"]["skill_name"], "daily-brief")
        self.assertEqual(kwargs["properties"]["skill_version"], 1)
        self.assertEqual(kwargs["properties"]["skill_origin"], "local")
        self.assertEqual(kwargs["properties"]["tool_ids"], ["sqlite_batch"])
        self.assertFalse(kwargs["properties"]["organization"])

    @patch("util.analytics.Analytics.track_event")
    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch", "read_file"})
    def test_sqlite_skill_update_creates_new_version(self, _mock_available_tools, mock_track_event):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="daily-brief",
            description="Daily digest workflow",
            version=1,
            tools=["sqlite_batch"],
            instructions="Collect updates and summarize.",
        )

        baseline = seed_sqlite_skills(self.agent)
        self.assertIsNotNone(baseline)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE "__agent_skills"
                SET instructions = ?, tools = ?
                WHERE name = ? AND version = 1;
                """,
                (
                    "Collect updates, summarize, and include blockers.",
                    '["sqlite_batch","read_file"]',
                    "daily-brief",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        result = apply_sqlite_skill_updates(self.agent, baseline)

        self.assertFalse(result.errors)
        self.assertTrue(result.changed)
        self.assertIn("daily-brief@2", result.created_versions)

        latest = (
            PersistentAgentSkill.objects.filter(agent=self.agent, name="daily-brief")
            .order_by("-version")
            .first()
        )
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.version, 2)
        self.assertEqual(latest.tools, ["sqlite_batch", "read_file"])
        self.assertEqual(
            latest.instructions,
            "Collect updates, summarize, and include blockers.",
        )
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.PERSISTENT_AGENT_SKILL_UPDATED)
        self.assertEqual(kwargs["properties"]["skill_name"], "daily-brief")
        self.assertEqual(kwargs["properties"]["skill_version"], 2)
        self.assertEqual(kwargs["properties"]["skill_origin"], "local")
        self.assertEqual(kwargs["properties"]["tool_ids"], ["sqlite_batch", "read_file"])

    @patch("util.analytics.Analytics.track_event")
    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch"})
    def test_sqlite_skill_update_rejects_unknown_tool_ids(self, _mock_available_tools, mock_track_event):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="weekly-brief",
            description="Weekly digest workflow",
            version=1,
            tools=["sqlite_batch"],
            instructions="Prepare weekly summary.",
        )

        baseline = seed_sqlite_skills(self.agent)
        self.assertIsNotNone(baseline)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE "__agent_skills"
                SET tools = ?
                WHERE name = ? AND version = 1;
                """,
                ('["sqlite_batch","unknown_tool"]', "weekly-brief"),
            )
            conn.commit()
        finally:
            conn.close()

        result = apply_sqlite_skill_updates(self.agent, baseline)

        self.assertEqual(result.created_versions, [])
        self.assertTrue(result.errors)
        self.assertIn("unknown canonical tool id(s)", result.errors[0])
        self.assertEqual(
            PersistentAgentSkill.objects.filter(agent=self.agent, name="weekly-brief").count(),
            1,
        )
        mock_track_event.assert_not_called()

    @patch("util.analytics.Analytics.track_event")
    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch"})
    def test_sqlite_skill_delete_by_name_removes_all_versions(self, _mock_available_tools, mock_track_event):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="ops-report",
            description="Ops report generation",
            version=1,
            tools=["sqlite_batch"],
            instructions="Generate report.",
        )
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="ops-report",
            description="Ops report generation",
            version=2,
            tools=["sqlite_batch"],
            instructions="Generate report with incident list.",
        )

        baseline = seed_sqlite_skills(self.agent)
        self.assertIsNotNone(baseline)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute('DELETE FROM "__agent_skills" WHERE name = ?;', ("ops-report",))
            conn.commit()
        finally:
            conn.close()

        result = apply_sqlite_skill_updates(self.agent, baseline)

        self.assertFalse(result.errors)
        self.assertTrue(result.changed)
        self.assertEqual(result.deleted_names, ["ops-report"])
        self.assertFalse(PersistentAgentSkill.objects.filter(agent=self.agent, name="ops-report").exists())
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.PERSISTENT_AGENT_SKILL_DELETED)
        self.assertEqual(kwargs["properties"]["skill_name"], "ops-report")
        self.assertEqual(kwargs["properties"]["skill_version"], 2)
        self.assertEqual(kwargs["properties"]["skill_origin"], "local")
        self.assertEqual(kwargs["properties"]["tool_ids"], ["sqlite_batch"])

    @patch("util.analytics.Analytics.track_event")
    @patch("api.agent.tools.tool_manager.get_available_tool_ids")
    def test_invalid_skill_row_does_not_delete_existing_versions(self, mock_available_tools, mock_track_event):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="ops-report",
            description="Ops report generation",
            version=1,
            tools=["sqlite_batch"],
            instructions="Generate report.",
        )

        baseline = seed_sqlite_skills(self.agent)
        self.assertIsNotNone(baseline)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE "__agent_skills"
                SET tools = ?
                WHERE name = ?;
                """,
                ('{"invalid": true}', "ops-report"),
            )
            conn.commit()
        finally:
            conn.close()

        result = apply_sqlite_skill_updates(self.agent, baseline)

        self.assertTrue(result.errors)
        self.assertIn("tools must be a JSON array", result.errors[0])
        self.assertEqual(result.deleted_names, [])
        self.assertFalse(result.changed)
        self.assertTrue(PersistentAgentSkill.objects.filter(agent=self.agent, name="ops-report").exists())
        mock_track_event.assert_not_called()
        mock_available_tools.assert_not_called()

    @patch("util.analytics.Analytics.track_event")
    @patch("api.agent.tools.tool_manager.get_available_tool_ids")
    def test_noop_skill_sync_skips_tool_discovery(self, mock_available_tools, mock_track_event):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="daily-brief",
            description="Daily digest workflow",
            version=1,
            tools=["sqlite_batch"],
            instructions="Collect updates and summarize.",
        )

        baseline = seed_sqlite_skills(self.agent)
        self.assertIsNotNone(baseline)

        result = apply_sqlite_skill_updates(self.agent, baseline)

        self.assertFalse(result.errors)
        self.assertFalse(result.changed)
        self.assertEqual(result.deleted_names, [])
        self.assertEqual(result.created_versions, [])
        mock_track_event.assert_not_called()
        mock_available_tools.assert_not_called()

    @patch("util.analytics.Analytics.track_event")
    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch", "read_file"})
    def test_sqlite_skill_update_forks_global_skill_source_on_local_edit(self, _mock_available_tools, mock_track_event):
        global_skill = GlobalAgentSkill.objects.create(
            name="daily-brief-template",
            description="Daily digest workflow",
            tools=["sqlite_batch"],
            instructions="Collect updates and summarize.",
        )
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            global_skill=global_skill,
            name="daily-brief-template",
            description="Daily digest workflow",
            version=1,
            tools=["sqlite_batch"],
            instructions="Collect updates and summarize.",
        )

        baseline = seed_sqlite_skills(self.agent)
        self.assertIsNotNone(baseline)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE "__agent_skills"
                SET instructions = ?, tools = ?
                WHERE name = ? AND version = 1;
                """,
                (
                    "Collect updates, summarize, and include blockers.",
                    '["sqlite_batch","read_file"]',
                    "daily-brief-template",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        result = apply_sqlite_skill_updates(self.agent, baseline)

        self.assertFalse(result.errors)
        self.assertIn("daily-brief-template@2", result.created_versions)
        latest = (
            PersistentAgentSkill.objects.filter(agent=self.agent, name="daily-brief-template")
            .order_by("-version")
            .first()
        )
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertIsNone(latest.global_skill)
        mock_track_event.assert_called_once()
        kwargs = mock_track_event.call_args.kwargs
        self.assertEqual(kwargs["event"], AnalyticsEvent.PERSISTENT_AGENT_GLOBAL_SKILL_FORKED)
        self.assertEqual(kwargs["properties"]["skill_name"], "daily-brief-template")
        self.assertEqual(kwargs["properties"]["skill_version"], 2)
        self.assertEqual(kwargs["properties"]["skill_origin"], "forked_from_global")
        self.assertEqual(kwargs["properties"]["global_skill_id"], str(global_skill.id))
        self.assertEqual(kwargs["properties"]["global_skill_name"], "daily-brief-template")
        self.assertEqual(kwargs["properties"]["tool_ids"], ["sqlite_batch", "read_file"])

    def test_prompt_block_uses_top_three_recently_used_skills(self):
        now = timezone.now()
        for idx in range(4):
            skill = PersistentAgentSkill.objects.create(
                agent=self.agent,
                name=f"skill-{idx}",
                description=f"description-{idx}",
                version=1,
                tools=["sqlite_batch"],
                instructions=f"instructions for skill {idx}",
            )
            PersistentAgentSkill.objects.filter(id=skill.id).update(
                updated_at=now + timedelta(minutes=idx),
                last_used_at=now + timedelta(hours=idx),
            )

        block = format_recent_skills_for_prompt(self.agent, limit=3)

        self.assertIn("Skill: skill-3 (v1)", block)
        self.assertIn("Skill: skill-2 (v1)", block)
        self.assertIn("Skill: skill-1 (v1)", block)
        self.assertIn("<skill_skill_3_v1>", block)
        self.assertIn("</skill_skill_3_v1>", block)
        self.assertIn("<skill_skill_2_v1>", block)
        self.assertIn("<skill_skill_1_v1>", block)
        self.assertNotIn("Skill: skill-0 (v1)", block)
        self.assertNotIn("<skill_skill_0_v1>", block)
        self.assertNotIn("System Skill: Runtime Planning\nKey:", block)
        self.assertLess(block.index("Skill: skill-1 (v1)"), block.index("Skill: skill-2 (v1)"))
        self.assertLess(block.index("Skill: skill-2 (v1)"), block.index("Skill: skill-3 (v1)"))
        self.assertIn("Omitted skills due to prompt limit:", block)
        self.assertIn("- skill-0", block)
        self.assertNotIn("System Skill: Runtime Planning", block)
        self.assertIn("Use `search_tools` with an exact omitted skill name or key", block)
        self.assertIn("instructions for skill 3", block)

    def test_prompt_block_disambiguates_saved_skill_tag_collisions(self):
        now = timezone.now()
        first = PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="Daily Brief",
            description="First daily brief",
            version=1,
            tools=["sqlite_batch"],
            instructions="Use first daily brief.",
        )
        second = PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="Daily-Brief",
            description="Second daily brief",
            version=1,
            tools=["sqlite_batch"],
            instructions="Use second daily brief.",
        )
        PersistentAgentSkill.objects.filter(id=first.id).update(last_used_at=now)
        PersistentAgentSkill.objects.filter(id=second.id).update(last_used_at=now + timedelta(minutes=1))

        block = format_recent_skills_for_prompt(self.agent, limit=2)

        self.assertIn("<skill_daily_brief_v1>", block)
        self.assertIn("</skill_daily_brief_v1>", block)
        self.assertIn("<skill_daily_brief_v1_2>", block)
        self.assertIn("</skill_daily_brief_v1_2>", block)
        self.assertIn("Skill: Daily Brief (v1)", block)
        self.assertIn("Skill: Daily-Brief (v1)", block)

    def test_prompt_block_does_not_include_runtime_planning_system_skill(self):
        self.assertNotIn("runtime_planning", default_enabled_system_skill_keys())

        block = format_recent_skills_for_prompt(self.agent, limit=1)

        self.assertEqual(block, "")

    def test_custom_tool_development_system_skill_is_not_default_enabled(self):
        self.assertNotIn(CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY, default_enabled_system_skill_keys())

        format_recent_skills_for_prompt(self.agent, limit=3)

        self.assertFalse(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
            ).exists()
        )

    def test_code_work_system_skill_is_registered_but_not_default_enabled(self):
        self.assertIn(CODE_WORK_SYSTEM_SKILL_KEY, DEFAULT_SYSTEM_SKILL_DEFINITIONS)
        self.assertNotIn(CODE_WORK_SYSTEM_SKILL_KEY, default_enabled_system_skill_keys())

        definition = DEFAULT_SYSTEM_SKILL_DEFINITIONS[CODE_WORK_SYSTEM_SKILL_KEY]
        self.assertIn("apply_patch", definition.tool_names)
        self.assertIn("run_command", definition.tool_names)
        self.assertIn("git status --short", definition.prompt_instructions)
        self.assertIn("$GOBII_SCRATCH_DIR", definition.prompt_instructions)
        self.assertIn("Scratch files do not sync into agent filespace", definition.prompt_instructions)
        self.assertIn("$GOBII_REPO_WORKDIR", definition.prompt_instructions)
        self.assertIn("Do not clone repos directly under `/workspace`", definition.prompt_instructions)
        self.assertNotIn("search_tools", definition.prompt_instructions)

    def test_image_generation_system_skill_is_registered_but_not_default_enabled(self):
        self.assertIn(IMAGE_GENERATION_SYSTEM_SKILL_KEY, DEFAULT_SYSTEM_SKILL_DEFINITIONS)
        self.assertNotIn(IMAGE_GENERATION_SYSTEM_SKILL_KEY, default_enabled_system_skill_keys())

        definition = IMAGE_GENERATION_SYSTEM_SKILL
        self.assertEqual(definition.tool_names, ("create_image",))
        self.assertIn("source_images", definition.prompt_instructions)
        self.assertIn("change only X; preserve Y unchanged", definition.prompt_instructions)
        self.assertIn("one `create_image` call per distinct requested asset", definition.prompt_instructions)
        self.assertIn("Do not use it for OCR", definition.prompt_instructions)
        self.assertIn("do not claim that Gobii verified an alpha channel", definition.prompt_instructions)
        self.assertIn("do not claim to have visually inspected", definition.prompt_instructions)
        self.assertNotIn("OPENAI_API_KEY", definition.prompt_instructions)
        self.assertNotIn("gpt-image", definition.prompt_instructions)
        self.assertNotIn("remove_chroma_key", definition.prompt_instructions)

    def test_prompt_block_renders_image_generation_system_skill(self):
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=IMAGE_GENERATION_SYSTEM_SKILL_KEY,
            is_enabled=True,
            last_used_at=timezone.now(),
        )

        block = format_recent_skills_for_prompt(self.agent, limit=1)

        self.assertIn("System Skill: Image Generation", block)
        self.assertIn("Tools: create_image", block)
        self.assertIn("Save each result to a descriptive path under `/exports/`", block)

    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=True)
    def test_enable_system_skill_accepts_available_create_custom_tool(self, _mock_sandbox):
        result = enable_system_skills(self.agent, [CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY])

        self.assertEqual(result["enabled"], [CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY])
        self.assertEqual(result["invalid"], [])
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
                is_enabled=True,
            ).exists()
        )
        self.assertTrue(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name=CREATE_CUSTOM_TOOL_NAME,
            ).exists()
        )

    def test_enable_and_refresh_system_skills_for_tool_creates_custom_tool_skill_state(self):
        refreshed = enable_and_refresh_system_skills_for_tool(self.agent, CREATE_CUSTOM_TOOL_NAME)

        self.assertEqual(refreshed, [CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY])
        state = PersistentAgentSystemSkillState.objects.get(
            agent=self.agent,
            skill_key=CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
        )
        self.assertTrue(state.is_enabled)
        self.assertIsNotNone(state.last_used_at)
        self.assertEqual(state.usage_count, 1)

        refreshed_again = enable_and_refresh_system_skills_for_tool(self.agent, CREATE_CUSTOM_TOOL_NAME)
        state.refresh_from_db()

        self.assertEqual(refreshed_again, [CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY])
        self.assertEqual(state.usage_count, 2)

    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False)
    def test_enable_system_skill_rejects_unavailable_create_custom_tool(self, _mock_sandbox):
        result = enable_system_skills(self.agent, [CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY])

        self.assertEqual(result["enabled"], [])
        self.assertEqual(result["invalid"], [CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY])
        self.assertFalse(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
            ).exists()
        )

    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=True)
    @patch("api.agent.tools.custom_tools.sandbox_compute_enabled_for_agent", return_value=True)
    def test_prompt_block_renders_custom_tool_skill_with_dynamic_state(
        self,
        _mock_custom_sandbox,
        _mock_static_sandbox,
    ):
        PersistentAgentCustomTool.objects.create(
            agent=self.agent,
            name="Alpha",
            tool_name="custom_alpha",
            description="Alpha sync tool",
            source_path="/tools/alpha.py",
            parameters_schema={"type": "object", "properties": {}},
        )
        PersistentAgentEnabledTool.objects.create(agent=self.agent, tool_full_name="custom_alpha")
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
            is_enabled=True,
            last_used_at=timezone.now(),
        )

        block = format_recent_skills_for_prompt(self.agent, limit=2)

        self.assertIn("System Skill: Custom Tool Development", block)
        self.assertIn(f"Tools: {CREATE_CUSTOM_TOOL_NAME}", block)
        self.assertIn("Use `create_custom_tool` to create or update sandboxed Python tools", block)
        self.assertIn("Current custom-tool state:", block)
        self.assertIn("Custom tools: 1 saved, 1 enabled.", block)
        self.assertIn("custom_alpha", block)

    @patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=False)
    def test_prompt_block_omits_custom_tool_skill_when_create_custom_tool_unavailable(self, _mock_sandbox):
        PersistentAgentSystemSkillState.objects.create(
            agent=self.agent,
            skill_key=CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
            is_enabled=True,
            last_used_at=timezone.now(),
        )

        block = format_recent_skills_for_prompt(self.agent, limit=3)

        self.assertNotIn("System Skill: Custom Tool Development", block)

    def test_prompt_block_limit_zero_omits_system_skills(self):
        block = format_recent_skills_for_prompt(self.agent, limit=0)

        self.assertEqual(block, "")

    def test_refresh_skills_for_tool_updates_saved_and_system_skills(self):
        saved = PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="sqlite-playbook",
            description="SQLite workflow",
            version=1,
            tools=["sqlite_batch"],
            instructions="Use sqlite.",
        )
        refresh_skills_for_tool(self.agent, "sqlite_batch")
        saved.refresh_from_db()

        self.assertIsNotNone(saved.last_used_at)
        self.assertEqual(saved.usage_count, 1)

        self.assertEqual(refresh_skills_for_tool(self.agent, "update_plan"), [])
        self.assertFalse(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key="runtime_planning",
            ).exists()
        )

    def test_prompt_block_reports_required_pending_and_missing_secrets(self):
        global_secret = GlobalSecret(
            user=self.user,
            secret_type=GlobalSecret.SecretType.ENV_VAR,
            domain_pattern=GlobalSecret.ENV_VAR_DOMAIN_SENTINEL,
            name="Available token",
            key="AVAILABLE_TOKEN",
        )
        global_secret.set_value("available-token")
        global_secret.save()

        PersistentAgentSecret.objects.create(
            agent=self.agent,
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
            domain_pattern=PersistentAgentSecret.ENV_VAR_DOMAIN_SENTINEL,
            name="Pending token",
            key="PENDING_TOKEN",
            requested=True,
            encrypted_value=b"",
        )

        secret_skill = PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="skill-with-secrets",
            description="Secret-heavy workflow",
            version=1,
            tools=["sqlite_batch"],
            secrets=[
                {
                    "name": "Available token",
                    "key": "AVAILABLE_TOKEN",
                    "secret_type": "env_var",
                    "description": "Already configured globally.",
                },
                {
                    "name": "Pending token",
                    "key": "PENDING_TOKEN",
                    "secret_type": "env_var",
                    "description": "Already requested.",
                },
                {
                    "name": "Missing credential",
                    "key": "portal_password",
                    "secret_type": "credential",
                    "domain_pattern": "*.example.com",
                    "description": "Still missing.",
                },
            ],
            instructions="Use the secrets when available.",
        )
        PersistentAgentSkill.objects.filter(id=secret_skill.id).update(last_used_at=timezone.now())

        block = format_recent_skills_for_prompt(self.agent, limit=1)

        self.assertIn("Required secrets:", block)
        self.assertIn("Available token [env_var:AVAILABLE_TOKEN]", block)
        self.assertIn("Pending token [env_var:PENDING_TOKEN]", block)
        self.assertIn("Missing credential [credential:portal_password @ https://*.example.com]", block)
        self.assertIn("Pending secrets: Pending token [env_var:PENDING_TOKEN]", block)
        self.assertIn("Missing secrets: Missing credential [credential:portal_password @ https://*.example.com]", block)
        self.assertIn("secure_credentials_request", block)
        self.assertIn("Follow up with the user", block)

    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch"})
    def test_sqlite_skill_update_persists_secret_changes(self, _mock_available_tools):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="daily-brief",
            description="Daily digest workflow",
            version=1,
            tools=["sqlite_batch"],
            secrets=[
                {
                    "name": "Old token",
                    "key": "OLD_TOKEN",
                    "secret_type": "env_var",
                    "description": "Old env key.",
                }
            ],
            instructions="Collect updates and summarize.",
        )

        baseline = seed_sqlite_skills(self.agent)
        self.assertIsNotNone(baseline)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE "__agent_skills"
                SET secrets = ?
                WHERE name = ? AND version = 1;
                """,
                (
                    '[{"name":"Renamed token","key":"RENAMED_TOKEN","secret_type":"env_var","description":"Updated env key."}]',
                    "daily-brief",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        result = apply_sqlite_skill_updates(self.agent, baseline)

        self.assertFalse(result.errors)
        self.assertTrue(result.changed)
        latest = (
            PersistentAgentSkill.objects.filter(agent=self.agent, name="daily-brief")
            .order_by("-version")
            .first()
        )
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.version, 2)
        self.assertEqual(
            latest.secrets,
            [
                {
                    "name": "Renamed token",
                    "key": "RENAMED_TOKEN",
                    "secret_type": "env_var",
                    "description": "Updated env key.",
                }
            ],
        )

    def test_skill_secret_validation_rejects_invalid_requirements(self):
        invalid_env_var_skill = GlobalAgentSkill(
            name="invalid-env",
            description="Invalid env var secret",
            tools=["sqlite_batch"],
            secrets=[
                {
                    "name": "Bad env",
                    "key": "bad-key",
                    "secret_type": "env_var",
                    "description": "Invalid env key.",
                }
            ],
            instructions="Do not use.",
        )
        with self.assertRaises(ValidationError):
            invalid_env_var_skill.full_clean()

        missing_domain_skill = PersistentAgentSkill(
            agent=self.agent,
            name="missing-domain",
            description="Missing credential domain",
            version=1,
            tools=["sqlite_batch"],
            secrets=[
                {
                    "name": "Portal password",
                    "key": "portal_password",
                    "secret_type": "credential",
                    "description": "Missing domain.",
                }
            ],
            instructions="Do not use.",
        )
        with self.assertRaises(ValidationError):
            missing_domain_skill.full_clean()


@tag("batch_agent_tools")
class AgentSkillToolEnablementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="skills-tools@example.com",
            email="skills-tools@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 100
        quota.save(update_fields=["agent_limit"])

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="skills-tools-browser-agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Skills Tool Agent",
            charter="Enforce skill tools",
            browser_use_agent=browser_agent,
        )

    @patch("api.agent.tools.tool_manager._build_available_tool_index", return_value={})
    def test_available_tool_ids_include_static_base_tools(self, _mock_catalog):
        available = get_available_tool_ids(self.agent)
        self.assertIn("apply_patch", available)
        self.assertIn("search_tools", available)
        self.assertIn("send_email", available)

    @patch("api.agent.tools.tool_manager._get_manager")
    @patch("api.agent.tools.tool_manager._build_available_tool_index", return_value={})
    def test_static_skill_tools_do_not_error_or_require_enable_rows(self, _mock_catalog, mock_get_manager):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="comms-skill",
            description="Use base comms tools",
            version=1,
            tools=["search_tools", "send_email"],
            instructions="Find information and email it.",
        )

        result = ensure_skill_tools_enabled(self.agent)

        self.assertFalse(result["invalid"])
        self.assertIn("search_tools", result["already_enabled"])
        self.assertIn("send_email", result["already_enabled"])
        self.assertEqual(
            PersistentAgentEnabledTool.objects.filter(
                agent=self.agent,
                tool_full_name__in=["search_tools", "send_email"],
            ).count(),
            0,
        )
        self.assertFalse(mock_get_manager.called)

    @patch("api.agent.tools.tool_manager.get_enabled_tool_limit", return_value=1)
    @patch("api.agent.tools.tool_manager._get_manager")
    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    def test_ensure_skill_tools_enabled_evicts_non_skill_tools(
        self,
        mock_catalog,
        mock_manager,
        _mock_limit,
    ):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="required-workflow",
            description="Requires read access",
            version=1,
            tools=["read_file"],
            instructions="Always read files before reporting.",
        )
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name="create_chart",
        )

        mock_manager.return_value.is_tool_blacklisted.return_value = False
        mock_catalog.return_value = {
            "read_file": ToolCatalogEntry(
                provider="builtin",
                full_name="read_file",
                description="Read files",
                parameters={},
                tool_server="builtin",
                tool_name="read_file",
                server_config_id=None,
            )
        }

        result = ensure_skill_tools_enabled(self.agent)

        self.assertFalse(result["invalid"])
        self.assertIn("read_file", result["required"])
        self.assertFalse(result["over_capacity"])
        self.assertTrue(PersistentAgentEnabledTool.objects.filter(agent=self.agent, tool_full_name="read_file").exists())
        self.assertFalse(PersistentAgentEnabledTool.objects.filter(agent=self.agent, tool_full_name="create_chart").exists())

    @patch("api.agent.tools.tool_manager.get_enabled_tool_limit", return_value=1)
    @patch("api.agent.tools.tool_manager._get_manager")
    @patch("api.agent.tools.tool_manager._build_available_tool_index")
    def test_ensure_skill_tools_enabled_reports_over_capacity_when_required_exceeds_cap(
        self,
        mock_catalog,
        mock_manager,
        _mock_limit,
    ):
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="required-workflow-a",
            description="Requires read access",
            version=1,
            tools=["read_file"],
            instructions="Read files.",
        )
        PersistentAgentSkill.objects.create(
            agent=self.agent,
            name="required-workflow-b",
            description="Requires sqlite access",
            version=1,
            tools=["sqlite_batch"],
            instructions="Use sqlite.",
        )

        mock_manager.return_value.is_tool_blacklisted.return_value = False
        mock_catalog.return_value = {
            "read_file": ToolCatalogEntry(
                provider="builtin",
                full_name="read_file",
                description="Read files",
                parameters={},
                tool_server="builtin",
                tool_name="read_file",
                server_config_id=None,
            ),
            "sqlite_batch": ToolCatalogEntry(
                provider="builtin",
                full_name="sqlite_batch",
                description="SQLite batch",
                parameters={},
                tool_server="builtin",
                tool_name="sqlite_batch",
                server_config_id=None,
            ),
        }

        result = ensure_skill_tools_enabled(self.agent)

        self.assertEqual(result["status"], "warning")
        self.assertTrue(result["over_capacity"])
        self.assertEqual(result["overflow_by"], 1)
        self.assertEqual(result["limit"], 1)
        self.assertEqual(result["total_enabled"], 2)
