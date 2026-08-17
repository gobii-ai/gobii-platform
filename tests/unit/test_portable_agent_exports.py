import hashlib
import json
import smtplib
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag

from api.agent.tools.sqlite_state import MESSAGES_TABLE, write_agent_sqlite_export_snapshot
from api.models import (
    AgentCollaborator,
    BrowserUseAgent,
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentError,
    PersistentAgentMessage,
    PersistentAgentPromptArchive,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
    PortableAgentExport,
    PortableAgentExportItem,
)
from api.services.portable_agent_export_archive import (
    AgentArchiveResult,
    ExportFileCollector,
    PortableAgentArchiveBuilder,
    _safe_relative_path,
)
from api.services.portable_agent_exports import (
    build_download_token,
    portable_agent_export_storage,
    user_can_access_export,
)
from api.tasks.portable_agent_exports import (
    _process_portable_agent_export,
    _send_completion_email,
    prune_portable_agent_exports,
)
from constants.feature_flags import PORTABLE_AGENT_EXPORTS


User = get_user_model()


class TemporaryStorageMixin:
    def setUp(self):
        super().setUp()
        self.storage_directory = tempfile.TemporaryDirectory()
        storage_config = {
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": self.storage_directory.name, "base_url": "/media/"},
            },
            "public_template_social_images": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": self.storage_directory.name, "base_url": "/media/"},
            },
            "portable_agent_exports": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {
                    "location": str(Path(self.storage_directory.name) / "exports"),
                    "base_url": "/private-agent-exports/",
                },
            },
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        self.storage_settings = override_settings(STORAGES=storage_config)
        self.storage_settings.enable()
        self.addCleanup(self.storage_settings.disable)
        self.addCleanup(self.storage_directory.cleanup)


@tag("agent_portable_export_batch")
@override_settings(SEGMENT_WRITE_KEY="", SEGMENT_WEB_WRITE_KEY="")
class PortableAgentExportApiTests(TemporaryStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(
            username="portable-owner",
            email="portable-owner@example.com",
            password="pw",
        )
        self.agent = self._create_agent(self.owner, "Migration Agent")
        self.client.force_login(self.owner)
        self.list_url = reverse("console_portable_agent_exports")

    @staticmethod
    def _create_agent(user, name, organization=None, *, is_active=True, is_deleted=False):
        browser = BrowserUseAgent.objects.create(user=user, name=f"{name} Browser")
        return PersistentAgent.objects.create(
            user=user,
            organization=organization,
            name=name,
            charter="Help with a portable migration.",
            browser_use_agent=browser,
            is_active=is_active,
            is_deleted=is_deleted,
        )

    def _post(self, payload):
        return self.client.post(
            self.list_url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _set_org_context(self, organization):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(organization.id)
        session["context_name"] = organization.name
        session.save()

    def test_flag_gates_creation_but_existing_jobs_remain_readable(self):
        with override_flag(PORTABLE_AGENT_EXPORTS, active=False):
            response = self._post({"scope": "agent", "agentId": str(self.agent.id)})
        self.assertEqual(response.status_code, 404)

        export = PortableAgentExport.objects.create(
            requester=self.owner,
            scope=PortableAgentExport.Scope.AGENT,
            scope_key=f"agent:{self.agent.id}",
            agent=self.agent,
            total_agents=1,
        )
        with override_flag(PORTABLE_AGENT_EXPORTS, active=False):
            response = self.client.get(self.list_url, {"scope": "agent", "agentId": str(self.agent.id)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["exports"][0]["id"], str(export.id))

    def test_active_job_is_idempotent_and_bulk_filters_deleted_agents(self):
        self._create_agent(self.owner, "Paused Agent", is_active=False)
        self._create_agent(self.owner, "Deleted Agent", is_deleted=True)
        with (
            override_flag(PORTABLE_AGENT_EXPORTS, active=True),
            patch("console.agent_exports_api.process_portable_agent_export.delay") as delay,
            patch("console.agent_exports_api.Analytics.track"),
        ):
            first = self._post({"scope": "personal"})
            second = self._post({"scope": "personal"})

        self.assertEqual(first.status_code, 202)
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(first.json()["export"]["id"], second.json()["export"]["id"])
        delay.assert_called_once()
        export = PortableAgentExport.objects.get(pk=first.json()["export"]["id"])
        self.assertEqual(export.total_agents, 2)
        self.assertEqual(set(export.items.values_list("source_agent_name", flat=True)), {"Migration Agent", "Paused Agent"})

    def test_collaborator_and_ordinary_org_member_cannot_export(self):
        collaborator = User.objects.create_user(username="portable-collab", email="collab@example.com")
        AgentCollaborator.objects.bulk_create([
            AgentCollaborator(agent=self.agent, user=collaborator, invited_by=self.owner),
        ])
        self.client.force_login(collaborator)
        with override_flag(PORTABLE_AGENT_EXPORTS, active=True):
            response = self._post({"scope": "agent", "agentId": str(self.agent.id)})
        self.assertEqual(response.status_code, 403)

        organization = Organization.objects.create(
            name="Portable Team",
            slug="portable-team",
            created_by=self.owner,
        )
        member = User.objects.create_user(username="portable-member", email="member@example.com")
        OrganizationMembership.objects.create(
            org=organization,
            user=member,
            role=OrganizationMembership.OrgRole.MEMBER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        self.client.force_login(member)
        self._set_org_context(organization)
        with override_flag(PORTABLE_AGENT_EXPORTS, active=True):
            response = self._post({"scope": "organization"})
        self.assertEqual(response.status_code, 403)

    def test_staff_status_does_not_override_natural_export_permissions(self):
        staff = User.objects.create_user(
            username="portable-staff",
            email="portable-staff@example.com",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(staff)
        with override_flag(PORTABLE_AGENT_EXPORTS, active=True):
            response = self._post({"scope": "agent", "agentId": str(self.agent.id)})
        self.assertEqual(response.status_code, 403)

    def test_org_admin_uses_current_context_and_cannot_choose_an_arbitrary_org(self):
        organization = Organization.objects.create(
            name="Exportable Team",
            slug="exportable-team",
            created_by=self.owner,
        )
        organization.billing.purchased_seats = 1
        organization.billing.save(update_fields=["purchased_seats"])
        admin = User.objects.create_user(username="portable-admin", email="admin@example.com")
        OrganizationMembership.objects.create(
            org=organization,
            user=admin,
            role=OrganizationMembership.OrgRole.ADMIN,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        org_agent = self._create_agent(self.owner, "Team Agent", organization=organization)
        self.client.force_login(admin)
        self._set_org_context(organization)

        with (
            override_flag(PORTABLE_AGENT_EXPORTS, active=True),
            patch("console.agent_exports_api.process_portable_agent_export.delay"),
            patch("console.agent_exports_api.Analytics.track"),
        ):
            response = self._post({"scope": "organization", "organizationId": "not-accepted"})

        self.assertEqual(response.status_code, 202)
        export = PortableAgentExport.objects.get(pk=response.json()["export"]["id"])
        self.assertEqual(export.organization, organization)
        self.assertEqual(export.items.get().source_agent_id, org_agent.id)

    def test_download_requires_sign_in_requester_token_and_current_access(self):
        export_storage = portable_agent_export_storage()
        storage_key = export_storage.save("portable-tests/export.zip", ContentFile(b"zip-data"))
        export = PortableAgentExport.objects.create(
            requester=self.owner,
            scope=PortableAgentExport.Scope.AGENT,
            scope_key=f"agent:{self.agent.id}",
            agent=self.agent,
            status=PortableAgentExport.Status.READY,
            total_agents=1,
            completed_agents=1,
            storage_key=storage_key,
            archive_filename="agent.zip",
            archive_size_bytes=8,
            expires_at=timezone.now() + timedelta(days=7),
        )
        PortableAgentExportItem.objects.create(
            export=export,
            agent=self.agent,
            source_agent_id=self.agent.id,
            source_agent_name=self.agent.name,
            folder_name=f"migration-agent--{str(self.agent.id)[:8]}",
            status=PortableAgentExportItem.Status.READY,
        )
        download_url = reverse("console_portable_agent_export_download", args=[export.id])
        token = build_download_token(export)

        self.client.logout()
        response = self.client.get(download_url, {"token": token})
        self.assertEqual(response.status_code, 302)

        wrong_user = User.objects.create_user(username="portable-wrong", email="wrong@example.com")
        self.client.force_login(wrong_user)
        response = self.client.get(download_url, {"token": token})
        self.assertEqual(response.status_code, 404)

        self.client.force_login(self.owner)
        with patch("console.agent_exports_api.load_download_token", side_effect=signing.SignatureExpired):
            response = self.client.get(download_url, {"token": token})
        self.assertEqual(response.status_code, 403)

        with patch("console.agent_exports_api.Analytics.track"):
            response = self.client.get(download_url, {"token": token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"zip-data")

        new_owner = User.objects.create_user(username="portable-new-owner", email="new-owner@example.com")
        self.agent.user = new_owner
        self.agent.save(update_fields=["user"])
        response = self.client.get(download_url, {"token": token})
        self.assertEqual(response.status_code, 403)
        export.refresh_from_db()
        self.assertEqual(export.status, PortableAgentExport.Status.FAILED)
        self.assertFalse(export_storage.exists(storage_key))


@tag("agent_portable_export_batch")
@override_settings(SEGMENT_WRITE_KEY="", SEGMENT_WEB_WRITE_KEY="")
class PortableAgentArchiveTests(TemporaryStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="archive-owner", email="archive@example.com")
        browser = BrowserUseAgent.objects.create(user=self.user, name="Archive Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="../Archive Agent",
            charter="Preserve visible work.",
            schedule="@daily",
            browser_use_agent=browser,
        )
        self.export = PortableAgentExport.objects.create(
            requester=self.user,
            scope=PortableAgentExport.Scope.AGENT,
            scope_key=f"agent:{self.agent.id}",
            agent=self.agent,
            total_agents=1,
        )
        self.item = PortableAgentExportItem.objects.create(
            export=self.export,
            agent=self.agent,
            source_agent_id=self.agent.id,
            source_agent_name=self.agent.name,
            folder_name=f"archive-agent--{str(self.agent.id)[:8]}",
            snapshot_at=timezone.now(),
        )

    @staticmethod
    def _write_test_sqlite(destination):
        connection = sqlite3.connect(destination)
        try:
            connection.execute("CREATE TABLE durable_memory (value TEXT NOT NULL)")
            connection.execute("INSERT INTO durable_memory (value) VALUES ('remember this')")
            connection.commit()
        finally:
            connection.close()

    def test_archive_contains_portable_structure_and_excludes_operational_data(self):
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="archive-user@example.com",
        )
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=f"agent-{self.agent.id}@example.com",
            is_primary=True,
        )
        visible_message = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            is_outbound=False,
            body="Keep this message, but hide sk-secretvalue123456.",
            raw_payload={"subject": "Portable subject", "rawOnly": "raw-payload-marker"},
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            is_outbound=False,
            body="hidden-message-marker",
            raw_payload={"hide_in_chat": True},
        )
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Visible tool work")
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="portable_tool",
            tool_params={"authorization": "Bearer secret-auth-value", "query": "safe"},
            result=json.dumps({
                "visible": "kept",
                "traceback": "tool-traceback-marker",
                "billing": "billing-marker",
            }),
        )
        hidden_step = PersistentAgentStep.objects.create(agent=self.agent, description="system-step-marker")
        PersistentAgentToolCall.objects.create(
            step=hidden_step,
            tool_name="hidden_system_tool",
            result="hidden-system-result-marker",
        )
        PersistentAgentSystemStep.objects.create(
            step=hidden_step,
            code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE,
            notes="hidden-reasoning-marker",
        )
        PersistentAgentError.objects.create(
            agent=self.agent,
            source="portable.test",
            message="internal-error-marker",
            traceback="server-traceback-marker",
        )
        PersistentAgentPromptArchive.objects.create(
            agent=self.agent,
            rendered_at=timezone.now(),
            storage_key="prompt-archive-marker",
            raw_bytes=100,
            compressed_bytes=50,
            tokens_before=10,
            tokens_after=8,
            tokens_saved=2,
        )
        self.item.snapshot_at = timezone.now()
        self.item.save(update_fields=["snapshot_at"])

        with tempfile.TemporaryDirectory() as destination:
            agent_dir = Path(destination) / self.item.folder_name
            with patch(
                "api.services.portable_agent_export_archive.write_agent_sqlite_export_snapshot",
                side_effect=lambda _agent_id, target: self._write_test_sqlite(target),
            ):
                result = PortableAgentArchiveBuilder(self.agent, self.item, agent_dir).build()

            expected_paths = {
                "manifest.json",
                "README.md",
                "identity/profile.json",
                "identity/instructions.md",
                "memory/current-state.md",
                "memory/snapshots.jsonl",
                "history/messages.jsonl",
                "history/transcript.md",
                "history/steps.jsonl",
                "history/tool-calls.jsonl",
                "work/plan.json",
                "work/tasks.json",
                "work/schedules.json",
                "work/pending-inputs.json",
                "state/sqlite/state.sqlite3",
                "state/sqlite/schema.sql",
                "state/sqlite/tables.json",
                "files/index.json",
                "communications/endpoints.json",
                "tools/capabilities.json",
                "tools/mcp-servers.json",
                "connections/requirements.json",
                "adapters/hermes/README.md",
                "adapters/manus/README.md",
                "adapters/chatgpt/README.md",
                "adapters/gemini/README.md",
            }
            actual_paths = {path.relative_to(agent_dir).as_posix() for path in agent_dir.rglob("*") if path.is_file()}
            self.assertTrue(expected_paths.issubset(actual_paths))
            self.assertNotIn("work/external-tasks.json", actual_paths)
            self.assertFalse((agent_dir / "adapters/hermes/skills").exists())
            self.assertFalse((agent_dir / "adapters/chatgpt/knowledge").exists())
            self.assertEqual(result.message_count, 1)
            self.assertEqual(result.step_count, 1)

            messages = [json.loads(line) for line in (agent_dir / "history/messages.jsonl").read_text().splitlines()]
            self.assertEqual(messages[0]["id"], str(visible_message.id))
            self.assertNotIn("raw_payload", messages[0])
            tool_call = json.loads((agent_dir / "history/tool-calls.jsonl").read_text().strip())
            self.assertEqual(tool_call["parameters"]["authorization"], "[REDACTED]")
            self.assertEqual(tool_call["result"]["traceback"], "[OMITTED]")
            self.assertEqual(tool_call["result"]["billing"], "[OMITTED]")
            schedules = json.loads((agent_dir / "work/schedules.json").read_text())
            self.assertFalse(schedules["schedules"][0]["enabledOnImport"])
            redaction_report = json.loads((agent_dir / "redaction-report.json").read_text())
            self.assertGreater(redaction_report["total"], 0)
            self.assertEqual(set(redaction_report), {"total", "counts"})

            text_bundle = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in agent_dir.rglob("*")
                if path.is_file() and path.suffix != ".sqlite3"
            )
            for forbidden in (
                "secretvalue123456",
                "raw-payload-marker",
                "hidden-message-marker",
                "system-step-marker",
                "hidden-system-result-marker",
                "hidden-reasoning-marker",
                "internal-error-marker",
                "server-traceback-marker",
                "tool-traceback-marker",
                "billing-marker",
                "prompt-archive-marker",
            ):
                self.assertNotIn(forbidden, text_bundle)

            connection = sqlite3.connect(agent_dir / "state/sqlite/state.sqlite3")
            try:
                self.assertEqual(connection.execute("SELECT value FROM durable_memory").fetchone()[0], "remember this")
            finally:
                connection.close()

    def test_paths_are_sanitized_and_duplicate_names_are_collision_safe(self):
        self.assertEqual(_safe_relative_path("../../secrets/../token.txt"), "secrets/token.txt")
        other = PersistentAgent(id=None, name=self.agent.name)
        other.id = self.agent.id.__class__("ffffffff-ffff-ffff-ffff-ffffffffffff")
        from api.services.portable_agent_exports import _agent_folder_name

        self.assertNotEqual(_agent_folder_name(self.agent), _agent_folder_name(other))
        self.assertNotIn("..", _agent_folder_name(self.agent))

    def test_file_deduplication_is_shared_across_agents(self):
        storage_name = default_storage.save("portable-tests/shared.txt", ContentFile(b"same content"))
        registry = {}
        with tempfile.TemporaryDirectory() as destination:
            root = Path(destination)
            first_result = AgentArchiveResult()
            first = ExportFileCollector(
                self.agent,
                root / "agents/first",
                first_result,
                registry,
                "agents/first",
            )
            first_entry = first.add_storage_file(
                storage_name=storage_name,
                logical_path="shared.txt",
                category="workspace",
                identifier="first",
            )
            second_result = AgentArchiveResult()
            second = ExportFileCollector(
                self.agent,
                root / "agents/second",
                second_result,
                registry,
                "agents/second",
            )
            second_entry = second.add_storage_file(
                storage_name=storage_name,
                logical_path="duplicate.txt",
                category="workspace",
                identifier="second",
            )

            self.assertFalse(second_entry["archivePath"].startswith("files/"))
            self.assertEqual(second_entry["archivePathScope"], "bundle")
            self.assertEqual(second_entry["archivePath"], f"agents/first/{first_entry['archivePath']}")
            self.assertEqual(first_result.file_count, 1)
            self.assertEqual(second_result.file_count, 0)

    def test_system_skills_export_setup_metadata_without_rendering_prompts(self):
        PersistentAgentSystemSkillState.objects.create(agent=self.agent, skill_key="private-system-skill")
        render_prompt = Mock(return_value="rendered-system-prompt-marker")
        definition = SimpleNamespace(
            skill_key="private-system-skill",
            name="Private system skill",
            tool_names=("system_tool",),
            setup_instructions="Reconnect the service.",
            setup_steps=("Sign in again.",),
            render_prompt_instructions=render_prompt,
        )
        server = SimpleNamespace(
            id="server-id",
            name="portable-server",
            display_name="Portable server",
            description="Reconnect me",
            scope="user",
            transport="streamable_http",
            command="",
            command_args=[],
            url="https://example.com/mcp?signature=private",
            auth_method="oauth2",
            managed_integration_key="portable",
            metadata={"scopes": ["records.read"], "internalSecret": "mcp-metadata-secret-marker"},
        )
        with tempfile.TemporaryDirectory() as destination:
            agent_dir = Path(destination) / self.item.folder_name
            builder = PortableAgentArchiveBuilder(self.agent, self.item, agent_dir)
            with (
                patch("api.services.portable_agent_export_archive.get_latest_skill_versions", return_value=[]),
                patch("api.services.portable_agent_export_archive.get_system_skill_definition", return_value=definition),
                patch("api.services.portable_agent_export_archive.agent_accessible_server_configs", return_value=[server]),
            ):
                builder._write_skills_tools_and_connections()

            render_prompt.assert_not_called()
            capabilities = json.loads((agent_dir / "tools/capabilities.json").read_text())
            self.assertEqual(capabilities["systemSkills"][0]["setupInstructions"], "Reconnect the service.")
            servers = json.loads((agent_dir / "tools/mcp-servers.json").read_text())["servers"]
            self.assertEqual(servers[0]["requiredScopes"], ["records.read"])
            self.assertNotIn("metadata", servers[0])
            self.assertFalse(any((agent_dir / "skills").glob("system-*")))
            exported_text = "\n".join(
                path.read_text(errors="ignore") for path in agent_dir.rglob("*") if path.is_file()
            )
            self.assertNotIn("rendered-system-prompt-marker", exported_text)
            self.assertNotIn("mcp-metadata-secret-marker", exported_text)

    def test_file_warning_does_not_export_storage_exception_details(self):
        with tempfile.TemporaryDirectory() as destination:
            result = AgentArchiveResult()
            collector = ExportFileCollector(
                self.agent,
                Path(destination),
                result,
                {},
                "agents/safe-warning",
            )
            with self.assertLogs("api.services.portable_agent_export_archive", level="WARNING") as logs:
                with patch(
                    "api.services.portable_agent_export_archive.default_storage.open",
                    side_effect=OSError("signed-url-and-credential-marker"),
                ):
                    entry = collector.add_storage_file(
                        storage_name="missing.txt",
                        logical_path="missing.txt",
                        category="workspace",
                        identifier="missing",
                    )

        self.assertTrue(entry["missing"])
        self.assertNotIn("signed-url-and-credential-marker", json.dumps(entry))
        self.assertNotIn("signed-url-and-credential-marker", json.dumps(result.warnings))
        self.assertNotIn("signed-url-and-credential-marker", "\n".join(logs.output))


@tag("agent_portable_export_batch")
class PortableAgentSQLiteSnapshotTests(TestCase):
    def test_snapshot_uses_coordinated_database_and_removes_ephemeral_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            destination = Path(directory) / "export.sqlite3"
            connection = sqlite3.connect(source)
            try:
                connection.execute("CREATE TABLE durable_state (value TEXT NOT NULL)")
                connection.execute("INSERT INTO durable_state (value) VALUES ('preserved')")
                connection.execute(f'CREATE TABLE "{MESSAGES_TABLE}" (value TEXT)')
                connection.execute(f'INSERT INTO "{MESSAGES_TABLE}" (value) VALUES (\'ephemeral\')')
                connection.commit()
            finally:
                connection.close()

            @contextmanager
            def coordinated_database():
                yield str(source)

            with patch(
                "api.agent.tools.sqlite_state.agent_sqlite_db",
                return_value=coordinated_database(),
            ) as coordinated:
                write_agent_sqlite_export_snapshot("agent-id", str(destination))

            coordinated.assert_called_once_with("agent-id")
            connection = sqlite3.connect(destination)
            try:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT value FROM durable_state").fetchone()[0], "preserved")
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                connection.close()
            self.assertNotIn(MESSAGES_TABLE, tables)


@tag("agent_portable_export_batch")
@override_settings(
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
    PUBLIC_SITE_URL="https://gobii.example",
    PORTABLE_AGENT_EXPORT_ARTIFACT_TTL_DAYS=7,
    PORTABLE_AGENT_EXPORT_METADATA_TTL_DAYS=30,
)
class PortableAgentExportTaskTests(TemporaryStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="task-owner", email="task-owner@example.com")
        EmailAddress.objects.create(user=self.user, email=self.user.email, verified=True, primary=True)

    def _create_agent(self, name):
        browser = BrowserUseAgent.objects.create(user=self.user, name=f"{name} Browser")
        return PersistentAgent.objects.create(
            user=self.user,
            name=name,
            charter="Portable task test",
            browser_use_agent=browser,
        )

    def _create_bulk_export(self, agents):
        export = PortableAgentExport.objects.create(
            requester=self.user,
            scope=PortableAgentExport.Scope.PERSONAL,
            scope_key=f"personal:{self.user.id}",
            total_agents=len(agents),
        )
        for agent in agents:
            PortableAgentExportItem.objects.create(
                export=export,
                agent=agent,
                source_agent_id=agent.id,
                source_agent_name=agent.name,
                folder_name=f"{agent.name.lower().replace(' ', '-')}--{str(agent.id)[:8]}",
            )
        return export

    def test_partial_bulk_success_packages_valid_checksums_and_completes_with_warnings(self):
        good_agent = self._create_agent("Good Agent")
        bad_agent = self._create_agent("Bad Agent")
        export = self._create_bulk_export([good_agent, bad_agent])

        def build(builder):
            if builder.agent.id == bad_agent.id:
                builder.content_registry["failed-digest"] = (
                    f"agents/{builder.item.folder_name}/files/workspace/source.txt"
                )
                raise OSError("missing source")
            self.assertNotIn("failed-digest", builder.content_registry)
            builder.destination.mkdir(parents=True, exist_ok=True)
            (builder.destination / "manifest.json").write_text('{"formatVersion":"gobii.agent-portable-export/v1"}\n')
            (builder.destination / "README.md").write_text("# Ready\n")
            return AgentArchiveResult(message_count=2, step_count=1)

        with (
            patch.object(PortableAgentArchiveBuilder, "build", autospec=True, side_effect=build),
            patch("api.tasks.portable_agent_exports._send_completion_email", return_value=False),
            patch("api.tasks.portable_agent_exports.Analytics.track"),
        ):
            _process_portable_agent_export(export)

        export.refresh_from_db()
        self.assertEqual(export.status, PortableAgentExport.Status.READY_WITH_WARNINGS)
        self.assertEqual(export.completed_agents, 1)
        self.assertEqual(export.failed_agents, 1)
        self.assertEqual(export.warning_count, 1)
        export_storage = portable_agent_export_storage()
        self.assertTrue(export_storage.exists(export.storage_key))

        with export_storage.open(export.storage_key, "rb") as archive_file, zipfile.ZipFile(archive_file) as archive:
            names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("README.md", names)
            self.assertIn("checksums.sha256", names)
            self.assertIn("shared-files/index.json", names)
            good_item = export.items.get(source_agent_id=good_agent.id)
            self.assertIn(f"agents/{good_item.folder_name}/manifest.json", names)
            bad_item = export.items.get(source_agent_id=bad_agent.id)
            self.assertNotIn(f"agents/{bad_item.folder_name}/manifest.json", names)
            for line in archive.read("checksums.sha256").decode().splitlines():
                expected_digest, path = line.split("  ", 1)
                self.assertEqual(hashlib.sha256(archive.read(path)).hexdigest(), expected_digest)

    def test_single_agent_email_prefers_agent_sender_then_falls_back(self):
        agent = self._create_agent("Email Agent")
        export = PortableAgentExport.objects.create(
            requester=self.user,
            scope=PortableAgentExport.Scope.AGENT,
            scope_key=f"agent:{agent.id}",
            agent=agent,
            status=PortableAgentExport.Status.READY,
            expires_at=timezone.now() + timedelta(days=7),
        )
        with (
            patch("api.tasks.portable_agent_exports._send_from_agent", return_value=True) as agent_send,
            patch("api.tasks.portable_agent_exports.send_mail") as system_send,
        ):
            self.assertTrue(_send_completion_email(export))
        agent_send.assert_called_once_with(export, self.user.email)
        system_send.assert_not_called()

        with (
            patch("api.tasks.portable_agent_exports._send_from_agent", return_value=False),
            patch("api.tasks.portable_agent_exports.send_mail", return_value=1) as system_send,
        ):
            self.assertTrue(_send_completion_email(export))
        system_send.assert_called_once()

        with (
            patch("api.tasks.portable_agent_exports._send_from_agent", return_value=False),
            patch(
                "api.tasks.portable_agent_exports.send_mail",
                side_effect=smtplib.SMTPException("delivery unavailable"),
            ),
        ):
            self.assertFalse(_send_completion_email(export))

    def test_permission_loss_revokes_ready_bulk_artifact(self):
        agent = self._create_agent("Transferred Agent")
        export = self._create_bulk_export([agent])
        item = export.items.get()
        item.status = PortableAgentExportItem.Status.READY
        item.save(update_fields=["status"])
        storage_key = portable_agent_export_storage().save("portable-tests/revoked.zip", ContentFile(b"data"))
        export.status = PortableAgentExport.Status.READY
        export.storage_key = storage_key
        export.expires_at = timezone.now() + timedelta(days=7)
        export.save(update_fields=["status", "storage_key", "expires_at"])

        other = User.objects.create_user(username="transferred-owner", email="transferred@example.com")
        agent.user = other
        agent.save(update_fields=["user"])
        export.refresh_from_db()
        self.assertFalse(user_can_access_export(self.user, export))

    def test_cleanup_expires_artifacts_and_removes_old_metadata(self):
        agent = self._create_agent("Cleanup Agent")
        export_storage = portable_agent_export_storage()
        storage_key = export_storage.save("portable-tests/expired.zip", ContentFile(b"data"))
        expired = PortableAgentExport.objects.create(
            requester=self.user,
            scope=PortableAgentExport.Scope.AGENT,
            scope_key=f"agent:{agent.id}",
            agent=agent,
            status=PortableAgentExport.Status.READY,
            storage_key=storage_key,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        stale = PortableAgentExport.objects.create(
            requester=self.user,
            scope=PortableAgentExport.Scope.PERSONAL,
            scope_key=f"old-personal:{self.user.id}",
            status=PortableAgentExport.Status.FAILED,
        )
        PortableAgentExport.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=31),
        )

        with patch("api.tasks.portable_agent_exports.Analytics.track"):
            result = prune_portable_agent_exports()

        expired.refresh_from_db()
        self.assertEqual(expired.status, PortableAgentExport.Status.EXPIRED)
        self.assertFalse(export_storage.exists(storage_key))
        self.assertFalse(PortableAgentExport.objects.filter(pk=stale.pk).exists())
        self.assertEqual(result, {"expired": 1, "deletedMetadata": 1})
