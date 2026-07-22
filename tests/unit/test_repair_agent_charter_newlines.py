from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag

from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_sqlite")
class RepairAgentCharterNewlinesCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="charter-repair@example.com",
            email="charter-repair@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Charter repair browser")
        cls.agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=browser_agent,
            name="Repairable agent",
            charter="Role\\n\\n## Scope\\n- First item\\nambiguous text",
        )
        other_browser_agent = BrowserUseAgent.objects.create(
            user=user,
            name="Other charter repair browser",
        )
        cls.other_agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=other_browser_agent,
            name="Other agent",
            charter="Other\\n- Item",
        )

    def test_dry_run_reports_without_persisting(self):
        stdout = StringIO()

        call_command(
            "repair_agent_charter_newlines",
            "--agent-id",
            str(self.agent.id),
            stdout=stdout,
        )

        self.agent.refresh_from_db()
        self.assertEqual(
            self.agent.charter,
            "Role\\n\\n## Scope\\n- First item\\nambiguous text",
        )
        self.assertIn("WOULD_REPAIR", stdout.getvalue())
        self.assertIn("remaining_literal_newlines=1", stdout.getvalue())
        self.assertIn("DRY_RUN inspected=1 repairable=1 repaired=0 ambiguous=1 failures=0", stdout.getvalue())

    def test_apply_uses_charter_update_path_and_is_idempotent(self):
        def update_charter(agent, params):
            agent.charter = params["new_charter"]
            agent.save(update_fields=["charter"])
            return {"status": "ok"}

        stdout = StringIO()
        with patch(
            "api.management.commands.repair_agent_charter_newlines.execute_update_charter",
            side_effect=update_charter,
        ) as update:
            call_command(
                "repair_agent_charter_newlines",
                "--apply",
                "--agent-id",
                str(self.agent.id),
                stdout=stdout,
            )
            call_command(
                "repair_agent_charter_newlines",
                "--apply",
                "--agent-id",
                str(self.agent.id),
                stdout=stdout,
            )

        self.agent.refresh_from_db()
        self.assertEqual(
            self.agent.charter,
            "Role\n\n## Scope\n- First item\\nambiguous text",
        )
        self.assertEqual(update.call_count, 1)
        self.assertIn("REPAIRED", stdout.getvalue())
        self.assertIn("AMBIGUOUS", stdout.getvalue())
