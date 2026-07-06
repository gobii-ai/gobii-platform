from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.files.filespace_service import write_bytes_to_dir
from api.agent.tools.run_command import get_run_command_tool
from api.models import AgentFsNode, BrowserUseAgent, PersistentAgent
from api.services.sandbox_filespace_sync import apply_filespace_push, build_filespace_pull_manifest


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
