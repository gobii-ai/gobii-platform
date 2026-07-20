import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

import zstandard as zstd
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.evals.scenarios.daily_credit_prompt import DailyCreditPromptNotNearLimitScenario
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentPromptArchive, PersistentAgentStep


@tag("eval_sim")
class DailyCreditPromptArchiveTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="daily-credit-archive@example.com")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Daily Credit Archive BA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Daily Credit Archive Agent",
            charter="Test prompt archive selection.",
            browser_use_agent=browser_agent,
        )

    @staticmethod
    def _archive(agent, *, rendered_at, storage_key, step=None):
        return PersistentAgentPromptArchive.objects.create(
            agent=agent,
            rendered_at=rendered_at,
            storage_key=storage_key,
            raw_bytes=100,
            compressed_bytes=50,
            tokens_before=10,
            tokens_after=10,
            tokens_saved=0,
            step=step,
        )

    def test_prompt_audit_skips_unattached_trajectory_judge_archive(self):
        after = timezone.now()
        self._archive(
            self.agent,
            rendered_at=after + timedelta(milliseconds=1),
            storage_key="judge.zst",
        )
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Agent response")
        agent_archive = self._archive(
            self.agent,
            rendered_at=after + timedelta(milliseconds=2),
            storage_key="agent.zst",
            step=step,
        )
        compressed = zstd.ZstdCompressor().compress(
            json.dumps(
                {
                    "system_prompt": "## Budget Awareness\nDaily limit progress: 50 / 100",
                    "user_prompt": "Draft the update.",
                }
            ).encode()
        )

        with patch(
            "api.evals.scenarios.daily_credit_prompt.default_storage.open",
            return_value=BytesIO(compressed),
        ) as storage_open:
            archive, content = DailyCreditPromptNotNearLimitScenario()._latest_prompt_archive_content(
                str(self.agent.id),
                after=after,
            )

        self.assertEqual(archive, agent_archive)
        self.assertIn("Daily limit progress: 50 / 100", content)
        storage_open.assert_called_once_with("agent.zst", "rb")
