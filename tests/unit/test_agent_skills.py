import os
import sqlite3
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.tools.sqlite_skills import (
    apply_sqlite_skill_updates,
    format_recent_skills_for_prompt,
    seed_sqlite_skills,
)
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path
from api.agent.tools.tool_manager import (
    ToolCatalogEntry,
    ensure_skill_tools_enabled,
    get_available_tool_ids,
)
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentSkill,
    UserQuota,
)


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

    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch", "read_file"})
    def test_sqlite_skill_update_creates_new_version(self, _mock_available_tools):
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

    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch"})
    def test_sqlite_skill_update_rejects_unknown_tool_ids(self, _mock_available_tools):
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

    @patch("api.agent.tools.tool_manager.get_available_tool_ids", return_value={"sqlite_batch"})
    def test_sqlite_skill_delete_by_name_removes_all_versions(self, _mock_available_tools):
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

    @patch("api.agent.tools.tool_manager.get_available_tool_ids")
    def test_invalid_skill_row_does_not_delete_existing_versions(self, mock_available_tools):
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
        mock_available_tools.assert_not_called()

    @patch("api.agent.tools.tool_manager.get_available_tool_ids")
    def test_noop_skill_sync_skips_tool_discovery(self, mock_available_tools):
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
        mock_available_tools.assert_not_called()

    def test_prompt_block_uses_top_three_latest_skills(self):
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
            PersistentAgentSkill.objects.filter(id=skill.id).update(updated_at=now + timedelta(minutes=idx))

        block = format_recent_skills_for_prompt(self.agent, limit=3)

        self.assertIn("Skill: skill-3 (v1)", block)
        self.assertIn("Skill: skill-2 (v1)", block)
        self.assertIn("Skill: skill-1 (v1)", block)
        self.assertNotIn("Skill: skill-0 (v1)", block)
        self.assertIn("instructions for skill 3", block)


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
