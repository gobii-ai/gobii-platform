import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
from pathlib import PurePosixPath

from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import DatabaseError
from django.utils.dateparse import parse_datetime
from django.utils.text import get_valid_filename

from api.agent.core.llm_config import resolve_intelligence_tier_for_owner
from api.agent.system_skills.registry import get_system_skill_definition
from api.agent.tools.custom_tools import validate_custom_tool_source_code
from api.agent.tools.sqlite_recovery import SQLiteStateError
from api.agent.tools.sqlite_state import EPHEMERAL_TABLES, agent_sqlite_db
from api.models import (
    AgentFileSpace,
    AgentFileSpaceAccess,
    AgentFsNode,
    CommsAllowlistEntry,
    CommsChannel,
    DeliveryStatus,
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
    PersistentAgentSkill,
    PersistentAgentStep,
    PersistentAgentStepSnapshot,
    PersistentAgentSystemSkillState,
    PersistentAgentToolCall,
    PortableAgentImport,
    PortableAgentImportItem,
    PortableAgentMigrationReport,
    build_web_agent_address,
    build_web_user_address,
)
from api.services.portable_agent_import_archive import PortableAgentImportArchive
from api.services.agent_sqlite_coordination import AGENT_SQLITE_COORDINATION_ERRORS
from api.utils.sqlite_files import SQLiteFileValidationError, backup_sqlite_file, validate_sqlite_file


MAX_AVATAR_BYTES = min(settings.MAX_FILE_SIZE, 10 * 1024 * 1024)
MAX_AVATAR_PIXELS = 25_000_000
SAFE_AVATAR_FORMATS = {"GIF": ".gif", "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
SKILL_FIELD_RE = re.compile(r"^(name|description|version):\s*(.+)$", re.MULTILINE)


class PortableAgentRestoreError(RuntimeError):
    pass


def _parse_time(value):
    return parse_datetime(str(value or "")) or None


def _safe_text(value, *, limit=None) -> str:
    text = str(value or "")
    return text[:limit] if limit is not None else text


def _bounded_int(value, *, minimum: int, maximum: int, default: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return min(max(value, minimum), maximum)


def _safe_logical_parts(value) -> list[str]:
    parts = []
    for raw_part in str(value or "").replace("\\", "/").split("/"):
        if raw_part in {"", ".", ".."}:
            continue
        part = (get_valid_filename(raw_part) or "imported-item")[:255]
        parts.append(part)
    return parts or ["imported-item"]


def _unique_filespace_name(user, proposed: str, *, exclude_id=None) -> str:
    base = _safe_text(proposed.strip() or "Imported files", limit=110)
    candidate = base
    suffix = 2
    names = AgentFileSpace.objects.filter(owner_user=user)
    if exclude_id is not None:
        names = names.exclude(pk=exclude_id)
    while names.filter(name=candidate).exists():
        candidate = f"{base[:112]} {suffix}"
        suffix += 1
    return candidate


def _archive_content_path(prefix: str, entry: dict) -> str | None:
    archive_path = entry.get("archivePath")
    if not isinstance(archive_path, str) or not archive_path:
        return None
    if entry.get("archivePathScope") == "bundle":
        return archive_path
    return f"{prefix}/{archive_path}"


def _ensure_directory(filespace, cache: dict[str, AgentFsNode], parts: list[str]) -> AgentFsNode | None:
    parent = None
    accumulated = []
    for part in parts:
        accumulated.append(part)
        key = "/".join(accumulated).casefold()
        if key not in cache:
            cache[key] = AgentFsNode.objects.create(
                filespace=filespace,
                parent=parent,
                node_type=AgentFsNode.NodeType.DIR,
                name=part,
            )
        parent = cache[key]
    return parent


def _write_file_node(
    *,
    filespace,
    cache,
    logical_path,
    payload: bytes,
    content_type="",
    checksum="",
    agent,
) -> AgentFsNode:
    parts = _safe_logical_parts(logical_path)
    parent = _ensure_directory(filespace, cache, parts[:-1])
    name = parts[-1]
    existing = AgentFsNode.objects.alive().filter(filespace=filespace, parent=parent, name=name).first()
    if existing:
        stem, suffix = os.path.splitext(name)
        name = f"{stem}-imported-{hashlib.sha256(payload).hexdigest()[:8]}{suffix}"[:255]
    node = AgentFsNode(
        filespace=filespace,
        parent=parent,
        node_type=AgentFsNode.NodeType.FILE,
        name=name,
        mime_type=_safe_text(content_type, limit=127),
        checksum_sha256=checksum or hashlib.sha256(payload).hexdigest(),
        created_by_agent=agent,
    )
    node.content.save(name, ContentFile(payload), save=False)
    node.save()
    return node


class PortableAgentRestorer:
    def __init__(self, archive: PortableAgentImportArchive, job: PortableAgentImport, item: PortableAgentImportItem):
        if item.imported_agent is None:
            raise PortableAgentRestoreError("The reserved agent shell is missing.")
        self.archive = archive
        self.job = job
        self.item = item
        self.agent = item.imported_agent
        self.prefix = f"agents/{item.folder_name}"
        self.warnings = list(item.warnings) if isinstance(item.warnings, list) else []
        self.file_entries: dict[str, dict] = {}
        self.filespaces: dict[str, AgentFileSpace] = {}
        self.directory_caches: dict[str, dict[str, AgentFsNode]] = {}
        self.message_map: dict[str, PersistentAgentMessage] = {}

    def restore(self) -> list[str]:
        profile = self.archive.json(f"{self.prefix}/identity/profile.json")
        self._restore_identity(profile)
        self._restore_files()
        self._restore_memory()
        deliverables = self._restore_work()
        self._restore_history()
        self._restore_deliverables(deliverables)
        self._restore_skills_and_tools()
        self._restore_contacts()
        self._restore_sqlite()
        self._create_report(profile)
        return self.warnings

    def _optional_json(self, relative_path: str) -> dict:
        name = f"{self.prefix}/{relative_path}"
        return self.archive.json(name) if self.archive.has(name) else {}

    def _optional_list(self, relative_path: str, key: str) -> list:
        value = self._optional_json(relative_path).get(key)
        return value if isinstance(value, list) else []

    def _read_file_payload(self, entry: dict, label: str) -> tuple[bytes, str] | None:
        content_path = _archive_content_path(self.prefix, entry)
        if not content_path or not self.archive.has(content_path):
            self.warnings.append(f"{label} was unavailable.")
            return None
        if isinstance(entry.get("sizeBytes"), int) and entry["sizeBytes"] > settings.MAX_FILE_SIZE:
            self.warnings.append(f"{label} exceeded the destination file limit and was skipped.")
            return None
        payload = self.archive.bytes(content_path, limit=settings.MAX_FILE_SIZE)
        digest = hashlib.sha256(payload).hexdigest()
        if entry.get("sha256") and entry["sha256"] != digest:
            raise PortableAgentRestoreError(f"{label} failed checksum verification during restoration.")
        return payload, digest

    def _create_filespace(self, name: str, *, is_default: bool) -> AgentFileSpace:
        filespace = AgentFileSpace.objects.create(
            name=_unique_filespace_name(self.agent.user, name),
            owner_user=self.agent.user,
            description=f"Imported from Gobii agent {self.item.source_agent_name}.",
        )
        AgentFileSpaceAccess.objects.create(
            filespace=filespace,
            agent=self.agent,
            role=AgentFileSpaceAccess.Role.OWNER,
            is_default=is_default,
        )
        return filespace

    def _save_validated(self, value, warning: str) -> bool:
        try:
            value.full_clean()
            value.save()
        except (ValidationError, DatabaseError):
            self.warnings.append(warning)
            return False
        return True

    def _restore_identity(self, profile: dict) -> None:
        owner = self.agent.organization or self.agent.user
        tier_key = _safe_text(profile.get("preferredIntelligenceTier"), limit=64) or None
        try:
            tier = resolve_intelligence_tier_for_owner(owner, tier_key)
        except ValueError:
            tier = resolve_intelligence_tier_for_owner(owner, None)
            self.warnings.append("The exported intelligence tier was unavailable; the workspace default was used.")
        if tier_key and tier.key != tier_key:
            self.warnings.append(f"Intelligence tier `{tier_key}` was mapped to `{tier.key}` for this workspace.")

        tags = profile.get("tags") if isinstance(profile.get("tags"), list) else []
        self.agent.charter = _safe_text(profile.get("charter"))
        self.agent.short_description = _safe_text(profile.get("shortDescription"), limit=280)
        self.agent.mini_description = _safe_text(profile.get("miniDescription"), limit=80)
        self.agent.visual_description = _safe_text(profile.get("visualDescription"))
        self.agent.tags = [_safe_text(tag, limit=100) for tag in tags[:20]]
        self.agent.preferred_llm_tier = tier
        self.agent.planning_state = PersistentAgent.PlanningState.SKIPPED
        self.agent.schedule = None
        self.agent.proactive_opt_in = False
        self.agent.save(update_fields=[
            "charter", "short_description", "mini_description", "visual_description", "tags",
            "preferred_llm_tier", "planning_state", "schedule", "proactive_opt_in", "updated_at",
        ])
        self._restore_avatar(profile)

    def _restore_avatar(self, profile: dict) -> None:
        avatar = profile.get("avatar") if isinstance(profile.get("avatar"), dict) else {}
        declared_path = avatar.get("archivePath")
        avatar_names = []
        if isinstance(declared_path, str) and declared_path:
            avatar_names.append(f"{self.prefix}/{declared_path}")
        avatar_names.extend(
            name
            for name in self.archive.names_under(f"{self.prefix}/identity")
            if "/avatar." in name and name not in avatar_names
        )
        if not avatar_names:
            return
        name = avatar_names[0]
        try:
            payload = self.archive.bytes(name, limit=MAX_AVATAR_BYTES)
            with Image.open(io.BytesIO(payload)) as image:
                if image.width * image.height > MAX_AVATAR_PIXELS:
                    raise Image.DecompressionBombError("avatar dimensions exceed the destination limit")
                image.verify()
                suffix = SAFE_AVATAR_FORMATS.get(image.format or "")
            if suffix is None:
                raise UnidentifiedImageError("unsupported avatar format")
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError):
            self.warnings.append("The exported avatar failed destination validation and was skipped.")
            return
        self.agent.avatar.save(f"imported-avatar{suffix}", ContentFile(payload), save=True)

    def _restore_files(self) -> None:
        index = self._optional_json("files/index.json")
        if not index:
            self.warnings.append("No workspace file index was present in the export.")
            return
        entries = index.get("files") if isinstance(index.get("files"), list) else []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                self.file_entries[str(entry["id"])] = entry

        metadata_by_id = {}
        for row in self._optional_list("files/filespaces.json", "filespaces"):
            if isinstance(row, dict) and row.get("sourceFilespaceId"):
                metadata_by_id[str(row["sourceFilespaceId"])] = row

        workspace_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("category") == "workspace"]
        grouped: dict[str, list[dict]] = {}
        for entry in workspace_entries:
            key = str(entry.get("filespaceId") or "default")
            grouped.setdefault(key, []).append(entry)
        for source_id, metadata in metadata_by_id.items():
            if metadata.get("role") == AgentFileSpaceAccess.Role.OWNER:
                grouped.setdefault(source_id, [])

        shared_entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("category") == "shared"]
        for entry in shared_entries:
            source_id = str(entry.get("filespaceId") or "shared")
            grouped.setdefault(source_id, []).append(entry)
            metadata_by_id.setdefault(source_id, {
                "name": entry.get("filespaceName") or "Imported shared files",
                "role": AgentFileSpaceAccess.Role.READER,
                "isDefault": False,
            })

        default_access = self.agent.filespace_access.select_related("filespace").filter(is_default=True).first()
        default_source_id = next(
            (
                source_id
                for source_id, metadata in metadata_by_id.items()
                if metadata.get("isDefault")
            ),
            None,
        )
        if default_source_id is None:
            default_source_id = next(
                (
                    source_id
                    for source_id in grouped
                    if metadata_by_id.get(source_id, {}).get("role")
                    in {None, AgentFileSpaceAccess.Role.OWNER}
                ),
                None,
            )

        restored_mapping = {}
        for source_id, rows in grouped.items():
            metadata = metadata_by_id.get(source_id, {})
            proposed = _safe_text(
                metadata.get("name")
                or (rows[0].get("filespaceName") if rows else None)
                or f"{self.agent.name} imported files",
                limit=128,
            )
            private_fallback = metadata.get("role") not in {None, AgentFileSpaceAccess.Role.OWNER}
            if private_fallback:
                proposed = f"{proposed[:100]} — imported files"
            if default_access is not None and source_id == default_source_id:
                filespace = default_access.filespace
                filespace.name = _unique_filespace_name(
                    self.agent.user,
                    proposed,
                    exclude_id=filespace.id,
                )
                filespace.description = f"Imported from Gobii agent {self.item.source_agent_name}."
                filespace.save(update_fields=["name", "description", "updated_at"])
            else:
                filespace = self._create_filespace(proposed, is_default=False)
            self.filespaces[source_id] = filespace
            restored_mapping[source_id] = str(filespace.id)
            cache: dict[str, AgentFsNode] = {}
            self.directory_caches[source_id] = cache
            for entry in sorted(rows, key=lambda value: str(value.get("logicalPath") or "")):
                parts = _safe_logical_parts(entry.get("logicalPath"))
                if entry.get("directory"):
                    _ensure_directory(filespace, cache, parts)
                    continue
                restored = self._read_file_payload(
                    entry,
                    f"Workspace file `{entry.get('logicalPath') or 'unknown'}`",
                )
                if restored is None:
                    continue
                payload, digest = restored
                _write_file_node(
                    filespace=filespace,
                    cache=cache,
                    logical_path=entry.get("logicalPath"),
                    payload=payload,
                    content_type=entry.get("contentType") or "",
                    checksum=digest,
                    agent=self.agent,
                )
        compatibility = dict(self.item.compatibility) if isinstance(self.item.compatibility, dict) else {}
        compatibility["restoredFilespaces"] = restored_mapping
        compatibility["filespaceAccess"] = [
            {
                "sourceFilespaceId": source_id,
                "name": row.get("name"),
                "role": row.get("role"),
                "isDefault": bool(row.get("isDefault")),
                "agentAccess": row.get("agentAccess") if isinstance(row.get("agentAccess"), list) else [],
            }
            for source_id, row in metadata_by_id.items()
        ]
        self.item.compatibility = compatibility
        self.item.save(update_fields=["compatibility", "updated_at"])

    def _restore_memory(self) -> None:
        previous = {"communications": None, "execution": None}
        for row in self.archive.iter_jsonl(f"{self.prefix}/memory/snapshots.jsonl"):
            kind = str(row.get("kind") or "")
            snapshot_until = _parse_time(row.get("snapshotUntil"))
            if kind not in previous or snapshot_until is None:
                self.warnings.append("An invalid memory snapshot was skipped.")
                continue
            model = PersistentAgentCommsSnapshot if kind == "communications" else PersistentAgentStepSnapshot
            snapshot = model.objects.create(
                agent=self.agent,
                previous_snapshot=previous[kind],
                snapshot_until=snapshot_until,
                summary=_safe_text(row.get("summary")),
            )
            created_at = _parse_time(row.get("createdAt"))
            if created_at:
                model.objects.filter(pk=snapshot.pk).update(created_at=created_at)
            previous[kind] = snapshot

    def _restore_work(self) -> list[dict]:
        deliverables = []
        plan = self._optional_json("work/plan.json")
        if plan:
            state = str(plan.get("state") or "")
            if state == PersistentAgent.PlanningState.COMPLETED:
                self.agent.planning_state = PersistentAgent.PlanningState.COMPLETED
                self.agent.planning_plan = _safe_text(plan.get("plan"))
                self.agent.planning_completed_at = _parse_time(plan.get("completedAt"))
            else:
                self.agent.planning_state = PersistentAgent.PlanningState.SKIPPED
                self.agent.planning_plan = ""
                self.agent.planning_completed_at = None
                if state == PersistentAgent.PlanningState.PLANNING:
                    self.warnings.append("In-progress source planning was preserved in the migration report but was not resumed.")
            self.agent.save(update_fields=["planning_state", "planning_plan", "planning_completed_at", "updated_at"])
            if isinstance(plan.get("deliverables"), list):
                deliverables = [row for row in plan["deliverables"] if isinstance(row, dict)]

        for row in self._optional_list("work/tasks.json", "tasks"):
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "")
            valid_statuses = {choice for choice, _label in PersistentAgentKanbanCard.Status.choices}
            card = PersistentAgentKanbanCard.objects.create(
                assigned_agent=self.agent,
                title=_safe_text(row.get("title") or "Imported task", limit=255),
                description=_safe_text(row.get("description")),
                status=status if status in valid_statuses else PersistentAgentKanbanCard.Status.TODO,
                priority=_bounded_int(
                    row.get("priority"),
                    minimum=-(2 ** 31),
                    maximum=(2 ** 31) - 1,
                ),
                completed_at=_parse_time(row.get("completedAt")),
            )
            updates = {}
            if _parse_time(row.get("createdAt")):
                updates["created_at"] = _parse_time(row.get("createdAt"))
            if _parse_time(row.get("updatedAt")):
                updates["updated_at"] = _parse_time(row.get("updatedAt"))
            if updates:
                PersistentAgentKanbanCard.objects.filter(pk=card.pk).update(**updates)
        return deliverables

    def _historical_endpoint(self, kind: str, source: dict | None = None) -> PersistentAgentCommsEndpoint:
        source_id = str((source or {}).get("id") or hashlib.sha256(json.dumps(source or {}, sort_keys=True).encode()).hexdigest()[:16])
        address = f"portable-import://{self.agent.id}/{kind}/{source_id}"[:512]
        owner_agent = self.agent if kind == "agent" else None
        endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.OTHER,
            address=address,
            defaults={"owner_agent": owner_agent, "is_primary": False},
        )
        return endpoint

    def _restore_history(self) -> None:
        agent_endpoint = None
        conversations: dict[str, PersistentAgentConversation] = {}
        parent_ids: dict[str, str] = {}
        for row in self.archive.iter_jsonl(f"{self.prefix}/history/messages.jsonl"):
            source_conversation = row.get("conversation") if isinstance(row.get("conversation"), dict) else {}
            source_conversation_id = str(source_conversation.get("id") or row.get("id") or "history")
            conversation = conversations.get(source_conversation_id)
            if conversation is None:
                channel = str(row.get("channel") or CommsChannel.OTHER)
                if channel not in {choice for choice, _label in CommsChannel.choices}:
                    channel = CommsChannel.OTHER
                conversation = PersistentAgentConversation.objects.create(
                    channel=channel,
                    address=f"portable-import://{self.agent.id}/conversation/{source_conversation_id}"[:512],
                    display_name=_safe_text(source_conversation.get("displayName") or "Imported history", limit=256),
                    owner_agent=self.agent,
                    is_peer_dm=False,
                )
                conversations[source_conversation_id] = conversation
            is_outbound = row.get("direction") == "outbound"
            external = row.get("recipient") if is_outbound else row.get("sender")
            external = external if isinstance(external, dict) else {}
            if is_outbound:
                if agent_endpoint is None:
                    agent_endpoint = self._historical_endpoint("agent")
                from_endpoint = agent_endpoint
            else:
                from_endpoint = self._historical_endpoint("external", external)
            message = PersistentAgentMessage.objects.create(
                is_outbound=is_outbound,
                from_endpoint=from_endpoint,
                conversation=conversation,
                owner_agent=self.agent,
                body=_safe_text(row.get("body")),
                raw_payload={
                    "source": "portable_agent_import",
                    "sourceMessageId": str(row.get("id") or ""),
                    "sourceChannel": row.get("channel"),
                    "sourceSender": row.get("sender"),
                    "sourceRecipient": row.get("recipient"),
                    "sourceCc": row.get("cc"),
                    "sourceBcc": row.get("bcc"),
                    "sourceConversation": source_conversation,
                    "sourceSubject": row.get("subject"),
                    "sourceDeliveryStatus": row.get("deliveryStatus"),
                },
                latest_status=DeliveryStatus.DELIVERED,
            )
            timestamp = _parse_time(row.get("timestamp"))
            if timestamp:
                PersistentAgentMessage.objects.filter(pk=message.pk).update(timestamp=timestamp)
            source_message_id = str(row.get("id") or "")
            if source_message_id:
                self.message_map[source_message_id] = message
            if row.get("parentMessageId"):
                parent_ids[str(message.id)] = str(row["parentMessageId"])
            self._restore_attachments(message, row.get("attachments"))

        for message_id, source_parent_id in parent_ids.items():
            parent = self.message_map.get(source_parent_id)
            if parent:
                PersistentAgentMessage.objects.filter(pk=message_id).update(parent=parent)

        tool_rows = {
            str(row.get("stepId")): row
            for row in self.archive.iter_jsonl(f"{self.prefix}/history/tool-calls.jsonl")
            if row.get("stepId")
        }
        tool_call_map = {}
        pending_parents = []
        for row in self.archive.iter_jsonl(f"{self.prefix}/history/steps.jsonl"):
            step = PersistentAgentStep.objects.create(
                agent=self.agent,
                description=_safe_text(row.get("description")),
                credits_cost=None,
                metered=True,
            )
            timestamp = _parse_time(row.get("timestamp"))
            if timestamp:
                PersistentAgentStep.objects.filter(pk=step.pk).update(created_at=timestamp)
            source_step_id = str(row.get("id") or "")
            tool_row = tool_rows.get(source_step_id, {})
            raw_status = str(tool_row.get("status") or row.get("status") or "complete")
            status = raw_status if raw_status in {PersistentAgentToolCall.Status.COMPLETE, PersistentAgentToolCall.Status.ERROR} else PersistentAgentToolCall.Status.COMPLETE
            tool_call = PersistentAgentToolCall.objects.create(
                step=step,
                tool_name=_safe_text(tool_row.get("toolName") or row.get("toolName") or "imported_tool", limit=256),
                tool_params=tool_row.get("parameters") if isinstance(tool_row.get("parameters"), dict) else {},
                result=json.dumps(tool_row.get("result"), ensure_ascii=False) if not isinstance(tool_row.get("result"), str) else tool_row.get("result"),
                display_metadata={
                    "portableImport": True,
                    "sourceDisplayMetadata": tool_row.get("displayMetadata"),
                },
                status=status,
            )
            tool_call_map[source_step_id] = tool_call
            if tool_row.get("parentStepId"):
                pending_parents.append((tool_call, str(tool_row["parentStepId"])))
        for tool_call, parent_id in pending_parents:
            parent = tool_call_map.get(parent_id)
            if parent:
                tool_call.parent_tool_call = parent
                tool_call.save(update_fields=["parent_tool_call"])

    def _restore_attachments(self, message, raw_attachments) -> None:
        for row in raw_attachments if isinstance(raw_attachments, list) else []:
            if not isinstance(row, dict):
                continue
            entry = self.file_entries.get(str(row.get("id") or ""), row)
            restored = self._read_file_payload(
                entry,
                f"Attachment `{row.get('filename') or 'unknown'}`",
            )
            if restored is None:
                continue
            payload, digest = restored
            filename = (get_valid_filename(os.path.basename(str(entry.get("logicalPath") or row.get("filename") or "attachment"))) or "attachment")[:512]
            PersistentAgentMessageAttachment.objects.create(
                message=message,
                file=ContentFile(payload, name=filename),
                content_type=_safe_text(entry.get("contentType"), limit=128),
                file_size=len(payload),
                filename=filename,
                content_sha256=digest,
            )

    def _restore_deliverables(self, rows: list[dict]) -> None:
        for row in rows:
            kind = str(row.get("kind") or "")
            if kind not in {choice for choice, _label in PersistentAgentPlanDeliverable.Kind.choices}:
                continue
            message = self.message_map.get(str(row.get("messageId") or ""))
            if kind == PersistentAgentPlanDeliverable.Kind.MESSAGE and message is None:
                self.warnings.append("A message deliverable could not be linked to imported history.")
                continue
            PersistentAgentPlanDeliverable.objects.create(
                agent=self.agent,
                kind=kind,
                label=_safe_text(row.get("label"), limit=255),
                path=_safe_text(row.get("path"), limit=1024),
                message=message,
                position=_bounded_int(row.get("position"), minimum=0, maximum=32767),
            )

    def _parse_v1_skill(self, name: str) -> dict | None:
        try:
            text = self.archive.bytes(name, limit=2 * 1024 * 1024).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        fields = {}
        for key, raw_value in SKILL_FIELD_RE.findall(text):
            try:
                fields[key] = json.loads(raw_value)
            except json.JSONDecodeError:
                fields[key] = raw_value.strip()
        body = text.split("---", 2)[-1].strip()
        return {
            "name": fields.get("name") or PurePosixPath(name).parent.name,
            "description": fields.get("description") or "",
            "version": fields.get("version") if isinstance(fields.get("version"), int) else 1,
            "tools": [],
            "secrets": [],
            "instructions": body,
        }

    def _restore_skills_and_tools(self) -> None:
        skill_index_name = f"{self.prefix}/skills/index.json"
        if self.archive.has(skill_index_name):
            skills = self._optional_list("skills/index.json", "skills")
        else:
            skills = [
                parsed
                for name in self.archive.names_under(f"{self.prefix}/skills")
                if name.endswith("/SKILL.md")
                for parsed in [self._parse_v1_skill(name)]
                if parsed is not None
            ]
        for row in skills:
            if not isinstance(row, dict):
                continue
            skill = PersistentAgentSkill(
                agent=self.agent,
                name=_safe_text(row.get("name") or "Imported skill", limit=128),
                description=_safe_text(row.get("description")),
                version=row.get("version") if isinstance(row.get("version"), int) and row["version"] > 0 else 1,
                tools=row.get("tools") if isinstance(row.get("tools"), list) else [],
                secrets=row.get("secrets") if isinstance(row.get("secrets"), list) else [],
                instructions=_safe_text(row.get("instructions")),
            )
            self._save_validated(skill, f"Saved skill `{skill.name}` was incompatible and was skipped.")

        capabilities = self._optional_json("tools/capabilities.json")
        if not capabilities:
            return
        for row in capabilities.get("systemSkills") if isinstance(capabilities.get("systemSkills"), list) else []:
            if not isinstance(row, dict) or not row.get("key"):
                continue
            key = _safe_text(row["key"], limit=128)
            if get_system_skill_definition(key) is None:
                self.warnings.append(f"System skill `{key}` is unavailable in this destination.")
                continue
            PersistentAgentSystemSkillState.objects.update_or_create(
                agent=self.agent,
                skill_key=key,
                defaults={"is_enabled": True},
            )
        try:
            from api.agent.tools.tool_manager import BUILTIN_TOOL_REGISTRY
        except ImportError:
            BUILTIN_TOOL_REGISTRY = {}
        for row in capabilities.get("enabledTools") if isinstance(capabilities.get("enabledTools"), list) else []:
            if not isinstance(row, dict) or row.get("type") != "builtin":
                continue
            full_name = str(row.get("fullName") or "")
            if full_name in BUILTIN_TOOL_REGISTRY:
                PersistentAgentEnabledTool.objects.get_or_create(
                    agent=self.agent,
                    tool_full_name=full_name,
                    defaults={
                        "tool_server": _safe_text(row.get("server"), limit=64),
                        "tool_name": _safe_text(row.get("name"), limit=128),
                    },
                )
            else:
                self.warnings.append(f"Built-in capability `{full_name or 'unknown'}` is unavailable here.")

        for row in capabilities.get("customTools") if isinstance(capabilities.get("customTools"), list) else []:
            self._restore_custom_tool(row)
        servers = self._optional_list("tools/mcp-servers.json", "servers")
        if servers:
            self.warnings.append(f"{len(servers)} MCP server connection(s) require review and reconnection.")

    def _restore_custom_tool(self, row) -> None:
        if not isinstance(row, dict):
            return
        tool_name = _safe_text(row.get("toolName"), limit=128)
        candidates = []
        if isinstance(row.get("sourceArchivePath"), str):
            candidates.append(f"{self.prefix}/{row['sourceArchivePath']}")
        safe_stem = get_valid_filename(tool_name) or "tool"
        candidates.extend(
            name for name in self.archive.names_under(f"{self.prefix}/tools/custom")
            if PurePosixPath(name).stem == safe_stem
        )
        source_name = next((name for name in candidates if self.archive.has(name)), None)
        if source_name is None:
            self.warnings.append(f"Custom tool `{tool_name or 'unknown'}` had no restorable source and was skipped.")
            return
        try:
            source = self.archive.bytes(source_name, limit=settings.MAX_FILE_SIZE).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            self.warnings.append(f"Custom tool `{tool_name or 'unknown'}` source was invalid and was skipped.")
            return
        source_path = _safe_text(row.get("sourcePath") or f"/tools/{safe_stem}.py", limit=512)
        validation_error = validate_custom_tool_source_code(source, source_path)
        if validation_error:
            self.warnings.append(f"Custom tool `{tool_name or 'unknown'}` failed validation and was skipped.")
            return
        if not self.filespaces:
            source_id = "default"
            default_access = self.agent.filespace_access.select_related("filespace").filter(is_default=True).first()
            if default_access is not None:
                filespace = default_access.filespace
            else:
                filespace = self._create_filespace(f"{self.agent.name} imported files", is_default=True)
            self.filespaces[source_id] = filespace
            self.directory_caches[source_id] = {}
        source_id, filespace = next(iter(self.filespaces.items()))
        if not AgentFsNode.objects.alive().files().filter(filespace=filespace, path=source_path).exists():
            _write_file_node(
                filespace=filespace,
                cache=self.directory_caches[source_id],
                logical_path=source_path,
                payload=source.encode("utf-8"),
                content_type="text/x-python",
                agent=self.agent,
            )
        tool = PersistentAgentCustomTool(
            agent=self.agent,
            name=_safe_text(row.get("name") or tool_name, limit=128),
            tool_name=tool_name,
            description=_safe_text(row.get("description")),
            source_path=source_path,
            parameters_schema=row.get("parametersSchema") if isinstance(row.get("parametersSchema"), dict) else {"type": "object", "properties": {}},
            entrypoint=_safe_text(row.get("entrypoint") or "run", limit=64),
            timeout_seconds=row.get("timeoutSeconds") if isinstance(row.get("timeoutSeconds"), int) else 300,
        )
        if not self._save_validated(
            tool,
            f"Custom tool `{tool_name or 'unknown'}` definition was incompatible and was skipped.",
        ):
            return
        self.warnings.append(f"Custom tool `{tool.tool_name}` was restored disabled and requires review.")

    def _restore_contacts(self) -> None:
        for row in self._optional_list("communications/contacts.json", "contacts"):
            if not isinstance(row, dict):
                continue
            channel = str(row.get("channel") or "")
            address = _safe_text(row.get("address"), limit=512).strip()
            if channel not in {choice for choice, _label in CommsChannel.choices} or not address:
                continue
            contact = CommsAllowlistEntry(
                agent=self.agent,
                channel=channel,
                address=address,
                verified=False,
                is_active=False,
                allow_inbound=False,
                allow_outbound=False,
                can_configure=False,
            )
            self._save_validated(contact, f"Reference contact `{address}` could not be restored.")

    def _restore_sqlite(self) -> None:
        source_name = f"{self.prefix}/state/sqlite/state.sqlite3"
        if not self.archive.has(source_name):
            return
        with tempfile.TemporaryDirectory(prefix="gobii-portable-sqlite-") as temp_dir:
            source_path = os.path.join(temp_dir, "source.sqlite3")
            with open(source_path, "wb") as output:
                output.write(self.archive.bytes(source_name, limit=settings.PORTABLE_AGENT_IMPORT_MAX_ARCHIVE_BYTES))
            try:
                validate_sqlite_file(source_path)
                with sqlite3.connect(source_path) as connection:
                    table_names = connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                    for table_name, in table_names:
                        if not table_name.startswith("__") and table_name not in EPHEMERAL_TABLES:
                            continue
                        escaped = table_name.replace('"', '""')
                        connection.execute(f'DROP TABLE IF EXISTS "{escaped}"')
                    connection.commit()
                validate_sqlite_file(source_path)
                with agent_sqlite_db(str(self.agent.id)) as destination_path:
                    backup_sqlite_file(source_path, destination_path)
            except (OSError, sqlite3.Error, SQLiteFileValidationError) as exc:
                raise PortableAgentRestoreError("The exported durable SQLite state could not be restored.") from exc

    def _create_report(self, profile: dict) -> None:
        schedules = self._optional_list("work/schedules.json", "schedules")
        if schedules:
            self.warnings.append(f"{len(schedules)} schedule(s) were preserved for review but remain disabled.")
        report = {
            "source": {
                "formatVersion": self.job.format_version,
                "agentId": str(self.item.source_agent_id),
                "agentName": self.item.source_agent_name,
                "snapshotAt": self.item.snapshot_at.isoformat() if self.item.snapshot_at else None,
                "wasActive": bool(profile.get("isActiveAtExport")),
                "lifeState": profile.get("lifeState"),
            },
            "ownerInstructions": {
                "source": profile.get("ownerInstructionsSource"),
                "text": profile.get("ownerInstructions") or "",
                "appliedToDestination": False,
            },
            "sourcePlanning": self._optional_json("work/plan.json"),
            "schedules": schedules,
            "sourceCommunicationPolicy": {
                "policy": self._optional_json("communications/allowlist.json"),
                "endpoints": self._optional_list("communications/endpoints.json", "endpoints"),
                "appliedToDestination": False,
            },
            "connectionRequirements": self._optional_json("connections/requirements.json"),
            "mcpServers": self._optional_list("tools/mcp-servers.json", "servers"),
            "sourceCapabilities": self._optional_json("tools/capabilities.json"),
            "sourceWebhooks": self._optional_json("communications/webhooks.json"),
            "disabledByPolicy": [
                "schedules", "proactive work", "webhooks", "external channels", "MCP connections",
                "custom tools", "pending input requests",
            ],
            "warnings": self.warnings,
        }
        PortableAgentMigrationReport.objects.update_or_create(
            agent=self.agent,
            defaults={
                "source_format_version": self.job.format_version,
                "source_agent_id": self.item.source_agent_id,
                "source_snapshot_at": self.item.snapshot_at,
                "source_was_active": bool(profile.get("isActiveAtExport")),
                "report": report,
            },
        )

    def activate_for_web_chat(self) -> None:
        user_address = build_web_user_address(self.job.requester_id, self.agent.id)
        agent_address = build_web_agent_address(self.agent.id)
        user_endpoint, _created = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=user_address,
            defaults={"owner_agent": None, "is_primary": False},
        )
        PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=agent_address,
            defaults={"owner_agent": self.agent, "is_primary": False},
        )
        self.agent.preferred_contact_endpoint = user_endpoint
        self.agent.is_active = True
        self.agent.life_state = PersistentAgent.LifeState.ACTIVE
        self.agent.schedule = None
        self.agent.proactive_opt_in = False
        self.agent.save(update_fields=[
            "preferred_contact_endpoint", "is_active", "life_state", "schedule", "proactive_opt_in", "updated_at",
        ])


RESTORE_ERRORS = (
    DatabaseError,
    OSError,
    PortableAgentRestoreError,
    ValidationError,
    ValueError,
    sqlite3.Error,
    SQLiteStateError,
    *AGENT_SQLITE_COORDINATION_ERRORS,
)
