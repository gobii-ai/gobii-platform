import hashlib
import io
import json
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag

from api.agent.tools.sqlite_state import agent_sqlite_db
from api.models import (
    AgentFileSpace,
    AgentFileSpaceAccess,
    AgentFsNode,
    AgentPeerLink,
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsAllowlistEntry,
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentCommsSnapshot,
    PersistentAgentConversation,
    PersistentAgentCustomTool,
    PersistentAgentEnabledTool,
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    PersistentAgentPlanDeliverable,
    PersistentAgentSchedule,
    PersistentAgentSkill,
    PersistentAgentStep,
    PersistentAgentToolCall,
    PortableAgentExport,
    PortableAgentExportItem,
    PortableAgentImport,
    PortableAgentImportArtifactCleanup,
    PortableAgentImportItem,
    PortableAgentMigrationReport,
)
from api.services.portable_agent_export_archive import PortableAgentArchiveBuilder
from api.services.portable_agent_import_archive import (
    FORMAT_V1,
    FORMAT_V2,
    PortableAgentImportArchiveError,
    validate_portable_agent_archive,
)
from api.services.portable_agent_import_restore import PortableAgentRestorer
from api.services.portable_agent_imports import (
    delete_failed_import_shells,
    portable_agent_import_storage,
    reserve_portable_agent_shells,
    retry_portable_agent_import_artifact_cleanups,
    try_portable_agent_import_artifact_cleanup,
)
from api.tasks.portable_agent_exports import _write_root_files, _zip_staging_directory
from api.tasks.portable_agent_imports import (
    process_portable_agent_import,
    prune_portable_agent_imports,
    validate_portable_agent_import,
)
from constants.feature_flags import PORTABLE_AGENT_IMPORTS


User = get_user_model()


def _json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def build_portable_zip(
    *,
    format_version=FORMAT_V1,
    agent_id=None,
    agent_name="Imported Helper",
    file_overrides=None,
    checksum_overrides=None,
) -> bytes:
    source_id = agent_id or uuid.uuid4()
    folder = f"{agent_name.lower().replace(' ', '-')}-{str(source_id).replace('-', '')[:8]}"
    prefix = f"agents/{folder}"
    now = timezone.now().isoformat()
    files = {
        "manifest.json": _json_bytes({
            "formatVersion": format_version,
            "agents": [{
                "id": str(source_id),
                "name": agent_name,
                "folder": prefix,
                "status": "ready",
                "snapshotAt": now,
            }],
        }),
        f"{prefix}/manifest.json": _json_bytes({
            "formatVersion": format_version,
            "agentId": str(source_id),
            "agentName": agent_name,
            "snapshotAt": now,
            "folderName": folder,
            "counts": {"messages": 0, "steps": 0, "files": 0, "warnings": 0},
        }),
        f"{prefix}/identity/profile.json": _json_bytes({
            "id": str(source_id),
            "name": agent_name,
            "charter": "Continue the source agent's research carefully.",
            "shortDescription": "Imported research helper",
            "miniDescription": "Research helper",
            "visualDescription": "A careful archivist",
            "tags": ["research", "portable"],
            "preferredIntelligenceTier": "standard",
            "isActiveAtExport": False,
            "lifeState": "expired",
            "proactiveOptIn": True,
            "planningState": "completed",
            "ownerInstructionsSource": "personal",
            "ownerInstructions": "Source-only owner policy",
        }),
        f"{prefix}/memory/snapshots.jsonl": b"",
        f"{prefix}/work/plan.json": _json_bytes({
            "state": "completed",
            "plan": "Preserved migration plan",
            "completedAt": now,
            "deliverables": [],
        }),
        f"{prefix}/work/tasks.json": _json_bytes({"tasks": []}),
        f"{prefix}/work/schedules.json": _json_bytes({
            "schedules": [{"id": "primary", "kind": "legacy", "expression": "0 * * * *"}],
            "importPolicy": "disabled",
        }),
        f"{prefix}/history/messages.jsonl": b"",
        f"{prefix}/history/steps.jsonl": b"",
        f"{prefix}/history/tool-calls.jsonl": b"",
        f"{prefix}/files/index.json": _json_bytes({"files": []}),
        f"{prefix}/communications/contacts.json": _json_bytes({"contacts": []}),
        f"{prefix}/communications/relationships.json": _json_bytes({"peerAgents": []}),
        f"{prefix}/tools/capabilities.json": _json_bytes({
            "enabledTools": [], "customTools": [], "systemSkills": [],
        }),
        f"{prefix}/tools/mcp-servers.json": _json_bytes({"servers": []}),
        f"{prefix}/connections/requirements.json": _json_bytes({
            "credentialsIncluded": False, "secretRequirements": [],
        }),
    }
    if format_version == FORMAT_V2:
        files[f"{prefix}/files/filespaces.json"] = _json_bytes({"filespaces": []})
        files[f"{prefix}/skills/index.json"] = _json_bytes({"skills": []})
    if file_overrides:
        for name, content in file_overrides.items():
            if content is None:
                files.pop(name, None)
            else:
                files[name] = content
    checksums = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in files.items()
    }
    checksums.update(checksum_overrides or {})
    checksum_text = "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("checksums.sha256", checksum_text)
    return output.getvalue()


def combine_portable_zips(*payloads: bytes) -> bytes:
    files = {}
    agents = []
    format_version = None
    for payload in payloads:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            current_format = manifest["formatVersion"]
            if format_version is not None and current_format != format_version:
                raise ValueError("Portable ZIP formats must match.")
            format_version = current_format
            agents.extend(manifest["agents"])
            for name in archive.namelist():
                if name.startswith("agents/") and not name.endswith("/"):
                    if name in files:
                        raise ValueError("Portable ZIP agent folders must be unique.")
                    files[name] = archive.read(name)
    files["manifest.json"] = _json_bytes({"formatVersion": format_version, "agents": agents})
    checksums = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in files.items()
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr(
            "checksums.sha256",
            "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())).encode(),
        )
    return output.getvalue()


class ImportStorageMixin:
    def setUp(self):
        super().setUp()
        self.storage_directory = tempfile.TemporaryDirectory()
        root = Path(self.storage_directory.name)
        storage_config = {
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": str(root / "media"), "base_url": "/media/"},
            },
            "portable_agent_exports": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": str(root / "exports")},
            },
            "portable_agent_imports": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": str(root / "imports")},
            },
            "public_template_social_images": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": str(root / "public")},
            },
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        self.storage_override = override_settings(STORAGES=storage_config)
        self.storage_override.enable()
        self.addCleanup(self.storage_override.disable)
        self.addCleanup(self.storage_directory.cleanup)

    def create_import_job(self, user, archive_bytes: bytes) -> PortableAgentImport:
        digest = hashlib.sha256(archive_bytes).hexdigest()
        job = PortableAgentImport.objects.create(
            requester=user,
            target_type=PortableAgentImport.TargetType.PERSONAL,
            target_key=f"personal:{user.id}",
            archive_filename="portable.zip",
            archive_size_bytes=len(archive_bytes),
            archive_sha256=digest,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        job.storage_key = portable_agent_import_storage().save(
            f"tests/{job.id}.zip",
            ContentFile(archive_bytes),
        )
        job.save(update_fields=["storage_key", "updated_at"])
        return job


@tag("agent_portable_import_batch")
@override_settings(
    GOBII_PROPRIETARY_MODE=False,
    PORTABLE_AGENT_IMPORTS_ENABLED=True,
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class PortableAgentImportApiTests(ImportStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="import-owner", email="import-owner@example.com", password="pw")
        self.client.force_login(self.user)
        self.list_url = reverse("console_portable_agent_imports")

    def upload(self, archive_bytes=None):
        archive = archive_bytes or build_portable_zip()
        with patch("console.agent_imports_api.validate_portable_agent_import.delay"):
            return self.client.post(
                self.list_url,
                {"archive": SimpleUploadedFile("portable.zip", archive, content_type="application/zip")},
            )

    def test_oss_default_upload_validate_select_and_import(self):
        response = self.upload()
        self.assertEqual(response.status_code, 202)
        job = PortableAgentImport.objects.get(pk=response.json()["import"]["id"])

        validate_portable_agent_import(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, PortableAgentImport.Status.AWAITING_SELECTION)
        item = job.items.get()
        self.assertEqual(item.status, PortableAgentImportItem.Status.AVAILABLE)
        self.assertTrue(any("v1 export" in warning for warning in item.warnings))
        recent = self.client.get(self.list_url).json()["imports"][0]
        self.assertNotIn("agents", recent)

        start_url = reverse("console_portable_agent_import_start", args=[job.id])
        payload = {"agents": [{"itemId": str(item.id), "name": "Restored Helper"}]}
        with patch("console.agent_imports_api.process_portable_agent_import.delay") as delay:
            first = self.client.post(start_url, json.dumps(payload), content_type="application/json")
            second = self.client.post(start_url, json.dumps(payload), content_type="application/json")
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        delay.assert_called_once()

        process_portable_agent_import(str(job.id))
        job.refresh_from_db()
        item.refresh_from_db()
        agent = item.imported_agent
        self.assertEqual(job.status, PortableAgentImport.Status.COMPLETED_WITH_WARNINGS)
        self.assertIsNotNone(agent)
        self.assertTrue(agent.is_active)
        self.assertEqual(agent.life_state, PersistentAgent.LifeState.ACTIVE)
        self.assertFalse(agent.proactive_opt_in)
        self.assertIsNone(agent.schedule)
        self.assertEqual(agent.charter, "Continue the source agent's research carefully.")
        self.assertEqual(agent.planning_plan, "Preserved migration plan")
        self.assertEqual(set(agent.comms_endpoints.values_list("channel", flat=True)), {CommsChannel.WEB})
        self.assertEqual(PersistentAgentSchedule.objects.filter(agent=agent).count(), 0)
        self.assertEqual(BrowserUseAgentTask.objects.filter(agent=agent.browser_use_agent).count(), 0)
        self.assertTrue(PortableAgentMigrationReport.objects.filter(agent=agent).exists())
        self.assertEqual(job.storage_key, "")

    def test_authentication_feature_gate_requester_isolation_and_expiration(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.list_url).status_code, 401)
        self.client.force_login(self.user)
        with override_settings(GOBII_PROPRIETARY_MODE=True), override_flag(PORTABLE_AGENT_IMPORTS, active=False):
            self.assertEqual(self.client.get(self.list_url).status_code, 404)
        with override_settings(GOBII_PROPRIETARY_MODE=True), override_flag(PORTABLE_AGENT_IMPORTS, active=True):
            self.assertEqual(self.client.get(self.list_url).status_code, 200)

        response = self.upload()
        job = PortableAgentImport.objects.get(pk=response.json()["import"]["id"])
        other = User.objects.create_user(username="other-importer", email="other@example.com")
        self.client.force_login(other)
        self.assertEqual(
            self.client.get(reverse("console_portable_agent_import_detail", args=[job.id])).status_code,
            404,
        )
        self.client.force_login(self.user)
        validate_portable_agent_import(str(job.id))
        PortableAgentImport.objects.filter(pk=job.id).update(expires_at=timezone.now())
        detail = self.client.get(reverse("console_portable_agent_import_detail", args=[job.id]))
        self.assertEqual(detail.json()["import"]["status"], PortableAgentImport.Status.EXPIRED)

    def test_org_permission_rechecked_and_capacity_and_names_cannot_race(self):
        organization = Organization.objects.create(name="Import Team", slug="import-team", created_by=self.user)
        membership = OrganizationMembership.objects.create(
            org=organization,
            user=self.user,
            role=OrganizationMembership.OrgRole.ADMIN,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(organization.id)
        session["context_name"] = organization.name
        session.save()
        response = self.upload()
        job = PortableAgentImport.objects.get(pk=response.json()["import"]["id"])
        validate_portable_agent_import(str(job.id))
        item = job.items.get()
        membership.status = OrganizationMembership.OrgStatus.REMOVED
        membership.save(update_fields=["status"])
        start_url = reverse("console_portable_agent_import_start", args=[job.id])
        response = self.client.post(
            start_url,
            json.dumps({"agents": [{"itemId": str(item.id), "name": "Team copy"}]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

        membership.status = OrganizationMembership.OrgStatus.ACTIVE
        membership.save(update_fields=["status"])
        with patch("api.services.portable_agent_imports.AgentService.get_agents_available", return_value=0):
            response = self.client.post(
                start_url,
                json.dumps({"agents": [{"itemId": str(item.id), "name": "Team copy"}]}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 409)

        PortableAgentImport.objects.filter(pk=job.id).update(
            status=PortableAgentImport.Status.COMPLETED,
        )
        membership.status = OrganizationMembership.OrgStatus.REMOVED
        membership.save(update_fields=["status"])
        detail_url = reverse("console_portable_agent_import_detail", args=[job.id])
        self.assertEqual(self.client.get(detail_url).status_code, 403)

    def test_destination_name_conflict_is_rejected_before_shell_creation(self):
        response = self.upload()
        job = PortableAgentImport.objects.get(pk=response.json()["import"]["id"])
        validate_portable_agent_import(str(job.id))
        item = job.items.get()
        browser = BrowserUseAgent.objects.create(user=self.user, name="Existing name")
        PersistentAgent.objects.create(
            user=self.user,
            browser_use_agent=browser,
            name="Existing name",
        )
        response = self.client.post(
            reverse("console_portable_agent_import_start", args=[job.id]),
            json.dumps({"agents": [{"itemId": str(item.id), "name": "existing NAME"}]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        job.refresh_from_db()
        self.assertEqual(job.status, PortableAgentImport.Status.AWAITING_SELECTION)

    def test_failed_upload_storage_and_cleanup(self):
        with patch.object(portable_agent_import_storage(), "save", side_effect=OSError("storage offline")):
            response = self.client.post(
                self.list_url,
                {"archive": SimpleUploadedFile("portable.zip", build_portable_zip(), content_type="application/zip")},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(PortableAgentImport.objects.count(), 0)

        response = self.upload()
        job = PortableAgentImport.objects.get(pk=response.json()["import"]["id"])
        validate_portable_agent_import(str(job.id))
        PortableAgentImport.objects.filter(pk=job.id).update(expires_at=timezone.now())
        result = prune_portable_agent_imports()
        job.refresh_from_db()
        self.assertEqual(result["expired"], 1)
        self.assertEqual(job.status, PortableAgentImport.Status.EXPIRED)
        self.assertEqual(job.storage_key, "")

        default_key = storages["default"].save("tests/orphaned-import-file.txt", ContentFile(b"orphaned"))
        cleanup = PortableAgentImportArtifactCleanup.objects.create(
            storage_alias="default",
            storage_key=default_key,
            source_import_id=job.id,
        )
        with patch.object(storages["default"], "delete", side_effect=OSError("storage offline")):
            self.assertFalse(try_portable_agent_import_artifact_cleanup(cleanup.id))
        self.assertEqual(retry_portable_agent_import_artifact_cleanups(), 1)
        self.assertFalse(PortableAgentImportArtifactCleanup.objects.filter(pk=cleanup.id).exists())
        self.assertFalse(storages["default"].exists(default_key))


@tag("agent_portable_import_batch")
@override_settings(PORTABLE_AGENT_IMPORT_MAX_ARCHIVE_BYTES=2 * 1024 * 1024, PORTABLE_AGENT_IMPORT_MAX_ENTRIES=200)
class PortableAgentImportArchiveSecurityTests(TestCase):
    def validate_bytes(self, payload: bytes):
        with tempfile.NamedTemporaryFile(suffix=".zip") as archive_file:
            archive_file.write(payload)
            archive_file.flush()
            return validate_portable_agent_archive(archive_file.name)

    def assert_archive_error(self, payload: bytes, code: str):
        with self.assertRaises(PortableAgentImportArchiveError) as caught:
            self.validate_bytes(payload)
        self.assertEqual(caught.exception.code, code)

    def test_corrupt_non_gobii_future_version_and_checksum_failures(self):
        self.assert_archive_error(b"not a zip", "bad_zip")
        self.assert_archive_error(build_portable_zip(format_version="other/export-v1"), "invalid_format")
        self.assert_archive_error(build_portable_zip(format_version="gobii.agent-portable-export/v99"), "unsupported_version")
        payload = build_portable_zip(checksum_overrides={"manifest.json": "0" * 64})
        self.assert_archive_error(payload, "checksum_mismatch")

    def test_traversal_symlink_encryption_duplicates_and_bomb_limits(self):
        base = build_portable_zip()
        for name, configure, code in (
            ("../escape", None, "unsafe_path"),
            ("MANIFEST.JSON", None, "duplicate_path"),
            ("unsafe-link", "symlink", "symlink"),
        ):
            source = io.BytesIO(base)
            output = io.BytesIO()
            with zipfile.ZipFile(source) as source_zip, zipfile.ZipFile(output, "w") as destination:
                for info in source_zip.infolist():
                    destination.writestr(info, source_zip.read(info.filename))
                extra = zipfile.ZipInfo(name)
                if configure == "symlink":
                    extra.create_system = 3
                    extra.external_attr = 0o120777 << 16
                destination.writestr(extra, b"extra")
            self.assert_archive_error(output.getvalue(), code)

        encrypted = bytearray(base)
        offset = 0
        while (offset := encrypted.find(b"PK\x01\x02", offset)) >= 0:
            flags = int.from_bytes(encrypted[offset + 8:offset + 10], "little") | 0x1
            encrypted[offset + 8:offset + 10] = flags.to_bytes(2, "little")
            offset += 4
        self.assert_archive_error(bytes(encrypted), "encrypted_archive")

        with override_settings(PORTABLE_AGENT_IMPORT_MAX_ARCHIVE_BYTES=512):
            self.assert_archive_error(
                build_portable_zip(file_overrides={"large.bin": b"a" * 1024}),
                "archive_too_large",
            )

    def test_v2_requires_restoration_metadata_and_ignores_additive_fields(self):
        payload = build_portable_zip(format_version=FORMAT_V2)
        validation = self.validate_bytes(payload)
        self.assertEqual(validation.format_version, FORMAT_V2)
        source_id = validation.candidates[0].source_agent_id
        folder = validation.candidates[0].folder_name
        missing = build_portable_zip(
            format_version=FORMAT_V2,
            agent_id=source_id,
            file_overrides={f"agents/{folder}/skills/index.json": None},
        )
        self.assert_archive_error(missing, "invalid_manifest")

        with override_settings(PORTABLE_AGENT_IMPORT_MAX_AGENTS=1):
            self.assert_archive_error(
                combine_portable_zips(build_portable_zip(), build_portable_zip()),
                "too_many_agents",
            )


@tag("agent_portable_import_batch")
@override_settings(
    GOBII_PROPRIETARY_MODE=False,
    PORTABLE_AGENT_IMPORTS_ENABLED=True,
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class PortableAgentBulkImportTests(ImportStorageMixin, TestCase):
    def build_export_archive(self, user, agents: list[PersistentAgent]) -> bytes:
        export = PortableAgentExport.objects.create(
            requester=user,
            scope=PortableAgentExport.Scope.PERSONAL,
            scope_key=f"personal:{user.id}",
            total_agents=len(agents),
        )
        items = []
        for index, agent in enumerate(agents):
            items.append(PortableAgentExportItem.objects.create(
                export=export,
                agent=agent,
                source_agent_id=agent.id,
                source_agent_name=agent.name,
                folder_name=f"bulk-{index}-{str(agent.id).replace('-', '')[:8]}",
                snapshot_at=timezone.now(),
                status=PortableAgentExportItem.Status.READY,
            ))
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = Path(temp_dir) / "bundle"
            results = {}
            for item in items:
                destination = staging / "agents" / item.folder_name
                results[str(item.id)] = PortableAgentArchiveBuilder(item.agent, item, destination).build()
            _write_root_files(staging, export, items, results)
            archive_path = Path(temp_dir) / "bulk.zip"
            _zip_staging_directory(staging, archive_path)
            return archive_path.read_bytes()

    def test_partial_failure_keeps_success_and_removes_failed_shell(self):
        user = User.objects.create_user(username="partial-import", email="partial@example.com")
        good_id = uuid.uuid4()
        bad_id = uuid.uuid4()
        bad_name = "Broken SQLite"
        bad_folder = f"{bad_name.lower().replace(' ', '-')}-{str(bad_id).replace('-', '')[:8]}"
        archive_bytes = combine_portable_zips(
            build_portable_zip(format_version=FORMAT_V2, agent_id=good_id, agent_name="Healthy copy"),
            build_portable_zip(
                format_version=FORMAT_V2,
                agent_id=bad_id,
                agent_name=bad_name,
                file_overrides={f"agents/{bad_folder}/state/sqlite/state.sqlite3": b"not a sqlite database"},
            ),
        )
        job = self.create_import_job(user, archive_bytes)
        validate_portable_agent_import(str(job.id))
        selections = [
            {"itemId": str(item.id), "name": f"Imported {index}"}
            for index, item in enumerate(job.items.order_by("source_agent_name"), start=1)
        ]
        with (
            patch("api.services.portable_agent_imports.AgentService.get_agents_available", return_value=10),
            patch("api.services.persistent_agents.AgentService.has_agents_available", return_value=True),
        ):
            reserve_portable_agent_shells(job, selections)
        process_portable_agent_import(str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, PortableAgentImport.Status.COMPLETED_WITH_WARNINGS)
        self.assertEqual(job.completed_agents, 1)
        self.assertEqual(job.failed_agents, 1)
        ready = job.items.get(status=PortableAgentImportItem.Status.READY)
        failed = job.items.get(status=PortableAgentImportItem.Status.FAILED)
        self.assertTrue(ready.imported_agent.is_active)
        self.assertIsNone(failed.imported_agent)
        self.assertEqual(PersistentAgent.objects.filter(user=user).count(), 1)
        self.assertEqual(BrowserUseAgent.objects.filter(user=user).count(), 1)
        self.assertEqual(AgentFileSpace.objects.filter(owner_user=user).count(), 1)

    def test_failed_shell_cleanup_removes_unowned_historical_endpoints(self):
        user = User.objects.create_user(username="cleanup-import", email="cleanup@example.com")
        job = self.create_import_job(user, build_portable_zip())
        validate_portable_agent_import(str(job.id))
        item = job.items.get()
        with (
            patch("api.services.portable_agent_imports.AgentService.get_agents_available", return_value=10),
            patch("api.services.persistent_agents.AgentService.has_agents_available", return_value=True),
        ):
            reserve_portable_agent_shells(
                job,
                [{"itemId": str(item.id), "name": "Cleanup copy"}],
            )
        item.refresh_from_db()
        agent_id = item.imported_agent_id
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.OTHER,
            address=f"portable-import://{agent_id}/external/source",
        )
        item.status = PortableAgentImportItem.Status.FAILED
        item.save(update_fields=["status", "updated_at"])

        delete_failed_import_shells(job, failed_item=item)

        self.assertFalse(PersistentAgentCommsEndpoint.objects.filter(pk=endpoint.pk).exists())

    def test_redelivery_recovers_interrupted_item_and_finishes_remaining_agents(self):
        user = User.objects.create_user(username="resume-import", email="resume@example.com")
        archive_bytes = combine_portable_zips(build_portable_zip(), build_portable_zip())
        job = self.create_import_job(user, archive_bytes)
        validate_portable_agent_import(str(job.id))
        selections = [
            {"itemId": str(item.id), "name": f"Resume {index}"}
            for index, item in enumerate(job.items.order_by("source_agent_name"), start=1)
        ]
        with (
            patch("api.services.portable_agent_imports.AgentService.get_agents_available", return_value=10),
            patch("api.services.persistent_agents.AgentService.has_agents_available", return_value=True),
        ):
            reserve_portable_agent_shells(job, selections)
        interrupted = job.items.order_by("source_agent_name").first()
        interrupted.status = PortableAgentImportItem.Status.PROVISIONING
        interrupted.save(update_fields=["status", "updated_at"])
        job.status = PortableAgentImport.Status.RUNNING
        job.processing_task_id = f"local:{job.id}"
        job.save(update_fields=["status", "processing_task_id", "updated_at"])

        process_portable_agent_import(str(job.id))

        job.refresh_from_db()
        interrupted.refresh_from_db()
        self.assertEqual(job.status, PortableAgentImport.Status.COMPLETED_WITH_WARNINGS)
        self.assertEqual(interrupted.status, PortableAgentImportItem.Status.FAILED)
        self.assertIsNone(interrupted.imported_agent)
        self.assertEqual(job.items.filter(status=PortableAgentImportItem.Status.READY).count(), 1)

    def test_activation_and_ready_transition_roll_back_together(self):
        user = User.objects.create_user(username="atomic-import", email="atomic@example.com")
        job = self.create_import_job(user, build_portable_zip())
        validate_portable_agent_import(str(job.id))
        item = job.items.get()
        with (
            patch("api.services.portable_agent_imports.AgentService.get_agents_available", return_value=10),
            patch("api.services.persistent_agents.AgentService.has_agents_available", return_value=True),
        ):
            reserve_portable_agent_shells(
                job,
                [{"itemId": str(item.id), "name": "Atomic copy"}],
            )

        activate = PortableAgentRestorer.activate_for_web_chat

        def activate_then_fail(restorer):
            activate(restorer)
            raise DatabaseError("completion write failed")

        with patch.object(PortableAgentRestorer, "activate_for_web_chat", new=activate_then_fail):
            process_portable_agent_import(str(job.id))

        job.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(job.status, PortableAgentImport.Status.FAILED)
        self.assertEqual(item.status, PortableAgentImportItem.Status.FAILED)
        self.assertIsNone(item.imported_agent)
        self.assertFalse(PersistentAgent.objects.filter(user=user).exists())

    def test_bulk_import_recreates_complete_sharing_and_subset_uses_private_fallback(self):
        user = User.objects.create_user(username="shared-import", email="shared@example.com")
        source_agents = []
        for name in ("Source owner", "Source reader"):
            browser = BrowserUseAgent.objects.create(user=user, name=f"{name} browser")
            source_agents.append(PersistentAgent.objects.create(
                user=user,
                browser_use_agent=browser,
                name=name,
                charter=f"Charter for {name}",
            ))
        shared = AgentFileSpace.objects.create(name="Shared migration files", owner_user=user)
        AgentFileSpaceAccess.objects.create(
            filespace=shared,
            agent=source_agents[0],
            role=AgentFileSpaceAccess.Role.OWNER,
            is_default=False,
        )
        AgentFileSpaceAccess.objects.create(
            filespace=shared,
            agent=source_agents[1],
            role=AgentFileSpaceAccess.Role.READER,
            is_default=False,
        )
        shared_node = AgentFsNode(
            filespace=shared,
            node_type=AgentFsNode.NodeType.FILE,
            name="shared.txt",
            checksum_sha256=hashlib.sha256(b"shared content").hexdigest(),
        )
        shared_node.content.save("shared.txt", ContentFile(b"shared content"), save=False)
        shared_node.save()
        AgentPeerLink.objects.create(
            agent_a=source_agents[0],
            agent_b=source_agents[1],
            created_by=user,
            is_enabled=True,
        )
        archive_bytes = self.build_export_archive(user, source_agents)

        job = self.create_import_job(user, archive_bytes)
        validate_portable_agent_import(str(job.id))
        selections = [
            {"itemId": str(item.id), "name": f"Complete {index}"}
            for index, item in enumerate(job.items.order_by("source_agent_name"), start=1)
        ]
        with (
            patch("api.services.portable_agent_imports.AgentService.get_agents_available", return_value=10),
            patch("api.services.persistent_agents.AgentService.has_agents_available", return_value=True),
        ):
            reserve_portable_agent_shells(job, selections)
        process_portable_agent_import(str(job.id))
        completed_items = list(job.items.order_by("source_agent_name").select_related("imported_agent"))
        imported_agents = [item.imported_agent for item in completed_items]
        restored_ids = {
            item.compatibility["restoredFilespaces"][str(shared.id)]
            for item in completed_items
        }
        self.assertEqual(len(restored_ids), 1)
        restored_shared_id = restored_ids.pop()
        self.assertEqual(
            AgentFileSpaceAccess.objects.get(filespace_id=restored_shared_id, agent=imported_agents[0]).role,
            AgentFileSpaceAccess.Role.OWNER,
        )
        self.assertEqual(
            AgentFileSpaceAccess.objects.get(filespace_id=restored_shared_id, agent=imported_agents[1]).role,
            AgentFileSpaceAccess.Role.READER,
        )
        imported_link = AgentPeerLink.objects.get(
            agent_a__in=imported_agents,
            agent_b__in=imported_agents,
        )
        self.assertFalse(imported_link.is_enabled)

        subset_job = self.create_import_job(user, archive_bytes)
        validate_portable_agent_import(str(subset_job.id))
        reader_item = subset_job.items.get(source_agent_id=source_agents[1].id)
        with (
            patch("api.services.portable_agent_imports.AgentService.get_agents_available", return_value=10),
            patch("api.services.persistent_agents.AgentService.has_agents_available", return_value=True),
        ):
            reserve_portable_agent_shells(
                subset_job,
                [{"itemId": str(reader_item.id), "name": "Subset reader"}],
            )
        process_portable_agent_import(str(subset_job.id))
        reader_item.refresh_from_db()
        self.assertTrue(any("private imported-files area" in warning for warning in reader_item.warnings))
        self.assertFalse(
            AgentPeerLink.objects.filter(agent_a=reader_item.imported_agent).exists()
            or AgentPeerLink.objects.filter(agent_b=reader_item.imported_agent).exists()
        )
        report_warnings = reader_item.imported_agent.portable_migration_report.report["warnings"]
        self.assertTrue(any("private imported-files area" in warning for warning in report_warnings))


@tag("agent_portable_import_batch")
@override_settings(
    GOBII_PROPRIETARY_MODE=False,
    PORTABLE_AGENT_IMPORTS_ENABLED=True,
    SEGMENT_WRITE_KEY="",
    SEGMENT_WEB_WRITE_KEY="",
)
class PortableAgentV2RoundTripTests(ImportStorageMixin, TestCase):
    def test_v2_export_archive_round_trips_identity_and_durable_sqlite(self):
        user = User.objects.create_user(username="roundtrip-owner", email="roundtrip@example.com")
        browser = BrowserUseAgent.objects.create(user=user, name="Source browser")
        source_agent = PersistentAgent.objects.create(
            user=user,
            browser_use_agent=browser,
            name="Roundtrip Source",
            charter="Preserve this v2 charter.",
            short_description="V2 source",
            is_active=True,
            schedule="0 * * * *",
            proactive_opt_in=True,
            planning_state=PersistentAgent.PlanningState.COMPLETED,
            planning_plan="Ship the preserved migration.",
            planning_completed_at=timezone.now(),
        )
        avatar = io.BytesIO()
        Image.new("RGB", (16, 16), color=(20, 80, 180)).save(avatar, format="PNG")
        source_agent.avatar.save("source-avatar.png", ContentFile(avatar.getvalue()), save=True)
        PersistentAgentCommsSnapshot.objects.create(
            agent=source_agent,
            snapshot_until=timezone.now(),
            summary="Remember the verified migration context.",
        )
        task = PersistentAgentKanbanCard.objects.create(
            assigned_agent=source_agent,
            title="Preserved task",
            description="Keep this task through migration.",
            status=PersistentAgentKanbanCard.Status.DONE,
            completed_at=timezone.now(),
        )
        PersistentAgentPlanDeliverable.objects.create(
            agent=source_agent,
            kind=PersistentAgentPlanDeliverable.Kind.FILE,
            label="Migration output",
            path="/reports/migration.md",
            position=0,
        )
        filespace_access = source_agent.filespace_access.get(is_default=True)
        filespace = filespace_access.filespace
        filespace.name = "Source workspace"
        filespace.save(update_fields=["name", "updated_at"])
        report_node = AgentFsNode(
            filespace=filespace,
            node_type=AgentFsNode.NodeType.FILE,
            name="migration.md",
            mime_type="text/markdown",
            checksum_sha256=hashlib.sha256(b"portable report").hexdigest(),
            created_by_agent=source_agent,
        )
        report_node.content.save("migration.md", ContentFile(b"portable report"), save=False)
        report_node.save()
        tool_source = (
            b"from _gobii_ctx import main\n\n"
            b"def run(params, ctx):\n    return {'message': 'restored'}\n\n"
            b"if __name__ == '__main__':\n    main(run)\n"
        )
        tools_dir = AgentFsNode.objects.create(
            filespace=filespace,
            node_type=AgentFsNode.NodeType.DIR,
            name="tools",
        )
        tool_node = AgentFsNode(
            filespace=filespace,
            parent=tools_dir,
            node_type=AgentFsNode.NodeType.FILE,
            name="portable_tool.py",
            mime_type="text/x-python",
            checksum_sha256=hashlib.sha256(tool_source).hexdigest(),
            created_by_agent=source_agent,
        )
        tool_node.content.save("portable_tool.py", ContentFile(tool_source), save=False)
        tool_node.save()
        PersistentAgentCustomTool.objects.create(
            agent=source_agent,
            name="Portable tool",
            tool_name="custom_portable_tool",
            description="A saved portable tool.",
            source_path="/tools/portable_tool.py",
            parameters_schema={"type": "object", "properties": {}},
            entrypoint="run",
            timeout_seconds=60,
        )
        PersistentAgentSkill.objects.create(
            agent=source_agent,
            name="Portable skill",
            description="A saved migration skill.",
            version=2,
            tools=[],
            secrets=[],
            instructions="Use the preserved workspace report.",
        )
        source_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=source_agent,
            channel=CommsChannel.WEB,
            address=f"web://agent/{source_agent.id}",
        )
        conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.WEB,
            address=f"web://user/{user.id}/agent/{source_agent.id}",
            display_name="Source conversation",
            owner_agent=source_agent,
        )
        source_message = PersistentAgentMessage.objects.create(
            owner_agent=source_agent,
            from_endpoint=source_endpoint,
            conversation=conversation,
            is_outbound=True,
            body="Visible source history",
        )
        PersistentAgentMessageAttachment.objects.create(
            message=source_message,
            file=ContentFile(b"attachment content", name="evidence.txt"),
            content_type="text/plain",
            file_size=len(b"attachment content"),
            filename="evidence.txt",
            content_sha256=hashlib.sha256(b"attachment content").hexdigest(),
        )
        source_step = PersistentAgentStep.objects.create(
            agent=source_agent,
            description="Historical tool work",
            credits_cost=None,
        )
        PersistentAgentToolCall.objects.create(
            step=source_step,
            tool_name="read_source",
            tool_params={"path": "/reports/migration.md"},
            result=json.dumps({"ok": True}),
            status=PersistentAgentToolCall.Status.COMPLETE,
        )
        CommsAllowlistEntry.objects.create(
            agent=source_agent,
            channel=CommsChannel.EMAIL,
            address="reference@example.com",
            is_active=True,
        )
        export = PortableAgentExport.objects.create(
            requester=user,
            scope=PortableAgentExport.Scope.AGENT,
            scope_key=f"agent:{source_agent.id}",
            agent=source_agent,
            total_agents=1,
        )
        item = PortableAgentExportItem.objects.create(
            export=export,
            agent=source_agent,
            source_agent_id=source_agent.id,
            source_agent_name=source_agent.name,
            folder_name=f"roundtrip-{str(source_agent.id).replace('-', '')[:8]}",
            snapshot_at=timezone.now(),
            status=PortableAgentExportItem.Status.READY,
        )

        def write_sqlite(_agent_id, destination):
            with sqlite3.connect(destination) as connection:
                connection.execute("CREATE TABLE durable_notes (value TEXT)")
                connection.execute("INSERT INTO durable_notes VALUES ('preserved')")
                connection.execute('CREATE TABLE "__messages" (value TEXT)')
                connection.execute("INSERT INTO __messages VALUES ('ephemeral')")
                connection.execute('CREATE TABLE "__future_runtime" (value TEXT)')
                connection.commit()

        with tempfile.TemporaryDirectory() as temp_dir:
            staging = Path(temp_dir) / "bundle"
            agent_dir = staging / "agents" / item.folder_name
            with patch(
                "api.services.portable_agent_export_archive.write_agent_sqlite_export_snapshot",
                side_effect=write_sqlite,
            ):
                result = PortableAgentArchiveBuilder(source_agent, item, agent_dir).build()
            _write_root_files(staging, export, [item], {str(item.id): result})
            archive_path = Path(temp_dir) / "roundtrip.zip"
            _zip_staging_directory(staging, archive_path)
            archive_bytes = archive_path.read_bytes()

        digest = hashlib.sha256(archive_bytes).hexdigest()
        job = PortableAgentImport.objects.create(
            requester=user,
            target_type=PortableAgentImport.TargetType.PERSONAL,
            target_key=f"personal:{user.id}",
            archive_filename="roundtrip.zip",
            archive_size_bytes=len(archive_bytes),
            archive_sha256=digest,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        job.storage_key = portable_agent_import_storage().save(
            f"roundtrip/{job.id}.zip",
            ContentFile(archive_bytes),
        )
        job.save(update_fields=["storage_key", "updated_at"])
        validate_portable_agent_import(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.format_version, FORMAT_V2)
        imported_item = job.items.get()
        reserve_portable_agent_shells(
            job,
            [{"itemId": str(imported_item.id), "name": "Roundtrip Imported"}],
        )
        process_portable_agent_import(str(job.id))
        imported_item.refresh_from_db()
        imported = imported_item.imported_agent
        self.assertIsNotNone(imported)
        self.assertEqual(imported.charter, source_agent.charter)
        self.assertTrue(bool(imported.avatar))
        self.assertEqual(imported.comms_snapshots.count(), 1)
        self.assertEqual(imported.kanban_cards.get().title, task.title)
        self.assertEqual(imported.plan_deliverables.get().path, "/reports/migration.md")
        imported_message = imported.agent_messages.get()
        self.assertEqual(imported_message.body, source_message.body)
        self.assertEqual(imported_message.attachments.get().filename, "evidence.txt")
        imported_step = imported.steps.get()
        self.assertIsNone(imported_step.credits_cost)
        self.assertTrue(imported_step.metered)
        self.assertEqual(imported_step.tool_call.tool_name, "read_source")
        imported_default_filespace = imported.filespace_access.get(is_default=True).filespace
        self.assertTrue(imported_default_filespace.nodes.filter(name="migration.md").exists())
        self.assertEqual(imported.skills.get().name, "Portable skill")
        imported_tool = imported.custom_tools.get()
        self.assertEqual(imported_tool.tool_name, "custom_portable_tool")
        self.assertFalse(
            PersistentAgentEnabledTool.objects.filter(
                agent=imported,
                tool_full_name=imported_tool.tool_name,
            ).exists()
        )
        imported_contact = imported.manual_allowlist.get(address="reference@example.com")
        self.assertFalse(imported_contact.is_active)
        self.assertFalse(imported_contact.allow_inbound)
        self.assertFalse(imported_contact.allow_outbound)
        with agent_sqlite_db(str(imported.id)) as database_path, sqlite3.connect(database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            self.assertIn("durable_notes", tables)
            self.assertNotIn("__messages", tables)
            self.assertNotIn("__future_runtime", tables)
            self.assertEqual(connection.execute("SELECT value FROM durable_notes").fetchone()[0], "preserved")
