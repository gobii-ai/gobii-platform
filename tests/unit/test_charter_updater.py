from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from unittest.mock import patch

from api.agent.tools.charter_updater import execute_update_charter
from api.models import BrowserUseAgent, PersistentAgent


@tag("batch_charter_tools")
class CharterUpdaterToolTests(TestCase):
    def setUp(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(
            username="charter-owner",
            email="charter@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Browser Agent",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Persistent Agent",
            charter="Initial charter",
            browser_use_agent=self.browser_agent,
        )

    def test_execute_update_charter_schedules_short_description(self) -> None:
        new_charter = "Provide executive summaries"

        with patch(
            "api.agent.tools.charter_updater.maybe_schedule_short_description",
            return_value=True,
        ) as mock_schedule:
            response = execute_update_charter(
                self.agent,
                {"new_charter": new_charter},
            )

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.charter, new_charter)
        mock_schedule.assert_called_once_with(self.agent)
        self.assertEqual(
            response,
            {
                "status": "ok",
                "message": "Charter updated successfully.",
                "auto_sleep_ok": True,
            },
        )
