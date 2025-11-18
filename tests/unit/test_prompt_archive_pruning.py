import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.test import TestCase, tag
from django.utils import timezone
from unittest.mock import patch

from api.agent.core.event_processing import _archive_rendered_prompt, get_prompt_token_budget
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentPromptArchive
from api.maintenance.prompt_archives import prune_prompt_archives_for_cutoff

User = get_user_model()


@tag("batch_event_processing")
class PromptArchivePruningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="prompt_prune@example.com",
            email="prompt_prune@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="PromptPruneBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="PromptPruneAgent",
            charter="Cleanup test agent",
            browser_use_agent=self.browser_agent,
        )
        self.storage_dir = tempfile.mkdtemp()
        self.storage = FileSystemStorage(location=self.storage_dir)
        self.storage_patch = patch('api.agent.core.event_processing.default_storage', self.storage)
        self.models_storage_patch = patch('api.models.default_storage', self.storage)
        self.storage_patch.start()
        self.models_storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        self.addCleanup(self.models_storage_patch.stop)
        self.addCleanup(lambda: shutil.rmtree(self.storage_dir, ignore_errors=True))

    def _make_archive(self, days_ago: int) -> PersistentAgentPromptArchive:
        key, _, _, archive_id = _archive_rendered_prompt(
            agent=self.agent,
            system_prompt="System prompt",
            user_prompt="User prompt",
            tokens_before=100,
            tokens_after=80,
            tokens_saved=20,
            token_budget=get_prompt_token_budget(self.agent),
        )
        self.assertIsNotNone(archive_id)
        archive = PersistentAgentPromptArchive.objects.get(id=archive_id)
        archive.rendered_at = timezone.now() - timedelta(days=days_ago)
        archive.save(update_fields=["rendered_at"])
        self.assertTrue(self.storage.exists(key))
        return archive

    def test_prune_prompt_archives_for_cutoff_deletes_old_entries(self):
        """Archives older than the cutoff should be deleted, keeping recent ones."""
        old_archive = self._make_archive(days_ago=30)
        recent_archive = self._make_archive(days_ago=5)

        cutoff = timezone.now() - timedelta(days=14)
        found, deleted = prune_prompt_archives_for_cutoff(cutoff)

        self.assertEqual(found, 1)
        self.assertEqual(deleted, 1)
        self.assertFalse(PersistentAgentPromptArchive.objects.filter(id=old_archive.id).exists())
        self.assertFalse(self.storage.exists(old_archive.storage_key))
        self.assertTrue(PersistentAgentPromptArchive.objects.filter(id=recent_archive.id).exists())
        self.assertTrue(self.storage.exists(recent_archive.storage_key))

    def test_management_command_supports_dry_run_and_commit(self):
        """Management command should respect dry-run flag before performing deletion."""
        archive = self._make_archive(days_ago=15)

        call_command("prune_prompt_archives", "--days=14", "--dry-run")
        self.assertTrue(PersistentAgentPromptArchive.objects.filter(id=archive.id).exists())
        self.assertTrue(self.storage.exists(archive.storage_key))

        call_command("prune_prompt_archives", "--days=14")
        self.assertFalse(PersistentAgentPromptArchive.objects.filter(id=archive.id).exists())
        self.assertFalse(self.storage.exists(archive.storage_key))
