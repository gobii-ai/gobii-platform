import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.tools.run_command import get_run_command_tool
from api.models import AgentFsNode, BrowserUseAgent, PersistentAgent
from api.services.sandbox_compute import _sanitize_env
from api.services.sandbox_filespace_sync import apply_filespace_push, build_filespace_pull_manifest
from api.services.sandbox_internal_paths import GOBII_REPO_WORKDIR_ENV, GOBII_SCRATCH_DIR_ENV


@tag("batch_agent_filesystem")
class SandboxRepoWorkdirTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="repo-workdir-user",
            email="repo-workdir-user@example.com",
            password="pw",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Repo Workdir Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Repo Workdir Agent",
            charter="repo workdir charter",
            browser_use_agent=browser_agent,
        )

    def test_pull_manifest_excludes_repo_workdir_and_heavy_dirs(self):
        for path in (
            "/.scratch/repos/repo/file.py",
            "/.scratch/tmp/notes.txt",
            "/plain-repo/.git/HEAD",
            "/node_modules/pkg/index.js",
            "/reports/out.txt",
        ):
            result = write_bytes_to_dir(
                agent=self.agent,
                content_bytes=b"content",
                extension="",
                mime_type="text/plain",
                path=path,
                overwrite=True,
            )
            self.assertEqual(result["status"], "ok")

        manifest = build_filespace_pull_manifest(self.agent)

        self.assertEqual(manifest["status"], "ok")
        self.assertEqual([entry["path"] for entry in manifest["files"]], ["/reports/out.txt"])

    def test_apply_filespace_push_ignores_repo_workdir_and_heavy_dirs(self):
        result = apply_filespace_push(
            self.agent,
            [
                {"path": "/.scratch/repos/repo/file.py", "content": "repo", "mime_type": "text/plain"},
                {"path": "/.scratch/tmp/notes.txt", "content": "scratch", "mime_type": "text/plain"},
                {"path": "/plain-repo/.git/HEAD", "content": "ref: refs/heads/main\n", "mime_type": "text/plain"},
                {"path": "/node_modules/pkg/index.js", "content": "pkg", "mime_type": "text/plain"},
                {"path": "/reports/out.txt", "content": "ok", "mime_type": "text/plain"},
            ],
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["skipped"], 4)
        self.assertFalse(AgentFsNode.objects.filter(path="/.scratch/repos/repo/file.py").exists())
        self.assertFalse(AgentFsNode.objects.filter(path="/.scratch/tmp/notes.txt").exists())
        self.assertFalse(AgentFsNode.objects.filter(path="/plain-repo/.git/HEAD").exists())
        self.assertFalse(AgentFsNode.objects.filter(path="/node_modules/pkg/index.js").exists())
        self.assertTrue(AgentFsNode.objects.filter(path="/reports/out.txt").exists())

    def test_run_command_tool_description_mentions_repo_workdir(self):
        description = get_run_command_tool()["function"]["description"]

        self.assertIn("$GOBII_SCRATCH_DIR", description)
        self.assertIn("$GOBII_REPO_WORKDIR", description)

    def test_sanitize_env_creates_configured_scratch_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scratch_dir = os.path.join(tmpdir, "scratch")
            repo_dir = os.path.join(scratch_dir, "repos")

            env = _sanitize_env(
                {
                    GOBII_SCRATCH_DIR_ENV: scratch_dir,
                    GOBII_REPO_WORKDIR_ENV: repo_dir,
                }
            )

            self.assertEqual(env[GOBII_SCRATCH_DIR_ENV], scratch_dir)
            self.assertEqual(env[GOBII_REPO_WORKDIR_ENV], repo_dir)
            self.assertTrue(os.path.isdir(scratch_dir))
            self.assertTrue(os.path.isdir(repo_dir))

    def test_sanitize_env_falls_back_when_default_scratch_is_unwritable(self):
        def fake_makedirs(path, exist_ok=False):
            if path.startswith("/workspace"):
                raise PermissionError("workspace is not writable")

        with (
            patch.dict("api.services.sandbox_compute.os.environ", {}, clear=True),
            patch("api.services.sandbox_compute.os.makedirs", side_effect=fake_makedirs),
            patch("api.services.sandbox_compute.os.access", return_value=True),
            patch("api.services.sandbox_compute.tempfile.gettempdir", return_value="/tmp"),
        ):
            env = _sanitize_env({})

        self.assertEqual(env[GOBII_SCRATCH_DIR_ENV], "/tmp/gobii/scratch")
        self.assertEqual(env[GOBII_REPO_WORKDIR_ENV], "/tmp/gobii/scratch/repos")
