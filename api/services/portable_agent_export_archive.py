import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

from cryptography.exceptions import InvalidTag
from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Q
from django.utils.text import get_valid_filename, slugify

from api.agent.system_skills.registry import get_system_skill_definition
from api.agent.tools.sqlite_recovery import SQLiteStateError
from api.agent.tools.sqlite_skills import get_latest_skill_versions
from api.agent.tools.sqlite_state import write_agent_sqlite_export_snapshot
from api.models import (
    AgentFileSpaceAccess,
    AgentFsNode,
    AgentPeerLink,
    GlobalSecret,
    PersistentAgent,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentSecret,
    PortableAgentExportItem,
)
from api.services.agent_owner_custom_instructions import (
    get_custom_instructions_for_organization_id,
    get_custom_instructions_for_user_id,
)
from api.services.agent_sqlite_coordination import AGENT_SQLITE_COORDINATION_ERRORS
from api.services.mcp_servers import agent_accessible_server_configs
from api.services.portable_agent_exports import STORAGE_ERRORS
from console.agent_chat.timeline import visible_agent_message_queryset, visible_tool_steps_queryset


logger = logging.getLogger(__name__)

SENSITIVE_KEY_RE = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|api[_-]?key|private[_-]?key|"
    r"client[_-]?secret|access[_-]?token|refresh[_-]?token|session[_-]?(?:id|key|token))",
    re.IGNORECASE,
)
FORBIDDEN_OPERATIONAL_KEY_RE = re.compile(
    r"(?:traceback|stack[_-]?trace|chain[_-]?of[_-]?thought|hidden[_-]?reasoning|"
    r"system[_-]?prompt|prompt[_-]?archive|billing|credits?[_-]?cost|"
    r"provider[_-]?routing|task[_-]?lease)",
    re.IGNORECASE,
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")
BEARER_RE = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
TOKEN_RE = re.compile(r"\b(?:sk|pk|rk|xox[baprs]|gh[pousr])[-_][A-Za-z0-9_-]{12,}\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SQLITE_EXPORT_ERRORS = (OSError, sqlite3.Error, SQLiteStateError, *AGENT_SQLITE_COORDINATION_ERRORS)


class PortableExportSizeLimitExceeded(RuntimeError):
    pass


@dataclass
class ExportArchiveBudget:
    max_bytes: int
    used_bytes: int = 0

    def reserve(self, size: int) -> None:
        if self.used_bytes + size > self.max_bytes:
            raise PortableExportSizeLimitExceeded("Portable export staging limit exceeded.")
        self.used_bytes += size

    def release(self, size: int) -> None:
        self.used_bytes = max(0, self.used_bytes - size)

    def sync(self, directory: Path) -> None:
        self.used_bytes = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        if self.used_bytes > self.max_bytes:
            raise PortableExportSizeLimitExceeded("Portable export staging limit exceeded.")


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def write_json_file(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _safe_segment(value: str, fallback: str = "item") -> str:
    normalized = get_valid_filename(os.path.basename(str(value or "").replace("\\", "/")))
    return normalized[:180] or fallback


def _safe_relative_path(value: str, fallback: str = "item") -> str:
    parts = []
    for part in str(value or "").replace("\\", "/").split("/"):
        if part in {"", ".", ".."}:
            continue
        parts.append(_safe_segment(part, fallback))
    return str(PurePosixPath(*parts)) if parts else fallback


def _quote_sqlite_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _safe_url_without_credentials(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if parsed.port:
            hostname = f"{hostname}:{parsed.port}"
    except ValueError:
        return "[redacted-url]"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


class ExportRedactor:
    def __init__(self, known_secret_values: list[str]):
        self.known_secret_values = sorted(
            {value for value in known_secret_values if isinstance(value, str) and value},
            key=len,
            reverse=True,
        )
        self.counts: Counter[str] = Counter()

    def _redact_url(self, match: re.Match) -> str:
        value = match.group(0)
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname or ""
            if parsed.port:
                hostname = f"{hostname}:{parsed.port}"
        except ValueError:
            self.counts["url"] += 1
            return "[redacted-url]"
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            self.counts["url_credentials_or_query"] += 1
            return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))
        return value

    def redact_text(self, value: str) -> str:
        result = str(value or "")
        for secret in self.known_secret_values:
            if len(secret) < 4 and result != secret:
                continue
            occurrences = result.count(secret)
            if occurrences:
                self.counts["managed_secret_value"] += occurrences
                result = result.replace(secret, "[REDACTED]")
        for pattern, reason in (
            (PRIVATE_KEY_RE, "private_key"),
            (BEARER_RE, "authorization"),
            (JWT_RE, "jwt"),
            (TOKEN_RE, "token"),
        ):
            result, count = pattern.subn("[REDACTED]", result)
            if count:
                self.counts[reason] += count
        return URL_RE.sub(self._redact_url, result)

    def redact(self, value, *, key: str = ""):
        if key and FORBIDDEN_OPERATIONAL_KEY_RE.search(key):
            self.counts["operational_field_omitted"] += 1
            return "[OMITTED]"
        if key and SENSITIVE_KEY_RE.search(key):
            self.counts["sensitive_field"] += 1
            return "[REDACTED]"
        if isinstance(value, Mapping):
            return {str(item_key): self.redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def report(self) -> dict:
        return {"total": sum(self.counts.values()), "counts": dict(sorted(self.counts.items()))}


def _load_known_secret_values(agent: PersistentAgent) -> list[str]:
    secret_rows = list(PersistentAgentSecret.objects.filter(agent=agent, requested=False))
    if agent.organization_id:
        secret_rows.extend(GlobalSecret.objects.filter(organization_id=agent.organization_id))
    else:
        secret_rows.extend(GlobalSecret.objects.filter(user_id=agent.user_id, organization__isnull=True))
    values: list[str] = []
    for secret in secret_rows:
        try:
            value = secret.get_value()
        except (ValueError, InvalidTag):
            logger.warning("Could not decrypt secret %s for export redaction", secret.id)
            continue
        if value:
            values.append(value)
    return values


@dataclass
class AgentArchiveResult:
    display_name: str = "Agent"
    message_count: int = 0
    step_count: int = 0
    file_count: int = 0
    warnings: list[str] = field(default_factory=list)
    shared_file_references: list[dict] = field(default_factory=list)
    redaction_report: dict = field(default_factory=dict)


class ExportFileCollector:
    def __init__(
        self, agent: PersistentAgent, agent_dir: Path, result: AgentArchiveResult,
        content_registry: dict[str, str], archive_prefix: str,
        archive_budget: ExportArchiveBudget | None = None,
    ):
        self.agent = agent
        self.agent_dir = agent_dir
        self.result = result
        self.content_registry = content_registry
        self.archive_prefix = archive_prefix.strip("/")
        self.archive_budget = archive_budget or ExportArchiveBudget(settings.PORTABLE_AGENT_EXPORT_MAX_ARCHIVE_BYTES)
        self.entries: list[dict] = []
        self.used_paths: set[str] = set()

    def _unique_path(self, proposed: str, identifier) -> str:
        if proposed not in self.used_paths:
            self.used_paths.add(proposed)
            return proposed
        stem, suffix = os.path.splitext(proposed)
        identifier_suffix = str(identifier).replace("-", "")[:8] or "copy"
        candidate = f"{stem}--{identifier_suffix}{suffix}"
        collision_index = 2
        while candidate in self.used_paths:
            candidate = f"{stem}--{identifier_suffix}-{collision_index}{suffix}"
            collision_index += 1
        self.used_paths.add(candidate)
        return candidate

    def add_storage_file(
        self,
        *,
        storage_name: str,
        logical_path: str,
        category: str,
        identifier,
        expected_sha256: str = "",
        content_type: str = "",
        size_bytes=None,
    ) -> dict:
        safe_path = self._unique_path(_safe_relative_path(logical_path), identifier)
        relative_target = str(PurePosixPath("files", category, safe_path))
        entry = {
            "id": str(identifier),
            "category": category,
            "logicalPath": logical_path,
            "archivePath": relative_target,
            "contentType": content_type or None,
            "sizeBytes": size_bytes,
        }
        target = self.agent_dir / relative_target
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        copied = 0
        try:
            with default_storage.open(storage_name, "rb") as source, target.open("wb") as destination:
                while chunk := source.read(1024 * 1024):
                    self.archive_budget.reserve(len(chunk))
                    copied += len(chunk)
                    destination.write(chunk)
                    digest.update(chunk)
        except PortableExportSizeLimitExceeded:
            target.unlink(missing_ok=True)
            self.archive_budget.release(copied)
            raise
        except STORAGE_ERRORS as exc:
            logger.warning(
                "Could not include portable export file agent=%s error=%s",
                self.agent.id,
                type(exc).__name__,
            )
            target.unlink(missing_ok=True)
            self.archive_budget.release(copied)
            warning = "A referenced file could not be included."
            self.result.warnings.append(warning)
            entry.update({"archivePath": None, "missing": True, "warning": warning})
            self.entries.append(entry)
            return entry

        actual_digest = digest.hexdigest()
        canonical_path = self.content_registry.get(actual_digest)
        if canonical_path:
            target.unlink(missing_ok=True)
            self.archive_budget.release(copied)
            own_prefix = f"{self.archive_prefix}/" if self.archive_prefix else ""
            if own_prefix and canonical_path.startswith(own_prefix):
                entry["archivePath"] = canonical_path.removeprefix(own_prefix)
            else:
                entry["archivePath"] = canonical_path
                entry["archivePathScope"] = "bundle"
            entry["deduplicated"] = True
        else:
            self.content_registry[actual_digest] = (
                f"{self.archive_prefix}/{relative_target}" if self.archive_prefix else relative_target
            )
            self.result.file_count += 1
        entry["sha256"] = actual_digest
        entry["sizeBytes"] = copied
        if expected_sha256 and expected_sha256 != actual_digest:
            warning = f"Checksum changed while exporting {logical_path}."
            self.result.warnings.append(warning)
            entry["warning"] = warning
        self.entries.append(entry)
        return entry

    def collect_workspace(self) -> None:
        accesses = list(
            AgentFileSpaceAccess.objects.filter(agent=self.agent)
            .select_related("filespace")
            .order_by("filespace__name")
        )
        access_agents_by_space: dict[str, list[dict]] = {}
        all_accesses = AgentFileSpaceAccess.objects.filter(
            filespace_id__in=[access.filespace_id for access in accesses],
        ).order_by("filespace_id", "agent_id")
        for related_access in all_accesses:
            access_agents_by_space.setdefault(str(related_access.filespace_id), []).append({
                "agentId": str(related_access.agent_id),
                "role": related_access.role,
                "isDefault": related_access.is_default,
            })
        write_json_file(self.agent_dir / "files/filespaces.json", {
            "filespaces": [
                {
                    "sourceFilespaceId": str(access.filespace_id),
                    "name": access.filespace.name,
                    "description": access.filespace.description,
                    "role": access.role,
                    "isDefault": access.is_default,
                    "ownedByExportedAgent": access.role == AgentFileSpaceAccess.Role.OWNER,
                    "agentAccess": access_agents_by_space.get(str(access.filespace_id), []),
                }
                for access in accesses
            ],
            "sharingPolicy": "recreate-when-all-owners-selected-otherwise-private-copy",
        })
        owned_ids = {
            access.filespace_id
            for access in accesses
            if access.is_default or access.role == AgentFileSpaceAccess.Role.OWNER
        }
        referenced_ids = {access.filespace_id for access in accesses} - owned_ids

        for node in (
            AgentFsNode.objects.alive().filter(filespace_id__in=owned_ids)
            .select_related("filespace")
            .order_by("filespace_id", "path", "name")
        ):
            logical_path = node.path or node.name
            if node.node_type == AgentFsNode.NodeType.DIR:
                self.entries.append({
                    "id": str(node.id),
                    "category": "workspace",
                    "logicalPath": logical_path,
                    "archivePath": None,
                    "directory": True,
                    "filespaceId": str(node.filespace_id),
                    "filespaceName": node.filespace.name,
                })
                continue
            if not node.content or not node.content.name:
                warning = f"Workspace file {logical_path} has no stored content."
                self.result.warnings.append(warning)
                self.entries.append({"id": str(node.id), "logicalPath": logical_path, "missing": True, "warning": warning})
                continue
            entry = self.add_storage_file(
                storage_name=node.content.name,
                logical_path=logical_path,
                category="workspace",
                identifier=node.id,
                expected_sha256=node.checksum_sha256 or "",
                content_type=node.mime_type,
                size_bytes=node.size_bytes,
            )
            entry.update({"filespaceId": str(node.filespace_id), "filespaceName": node.filespace.name})

        owner_accesses = AgentFileSpaceAccess.objects.filter(
            filespace_id__in=referenced_ids,
            role=AgentFileSpaceAccess.Role.OWNER,
        ).select_related("agent")
        owners_by_space: dict[str, list[dict]] = {}
        for access in owner_accesses:
            owners_by_space.setdefault(str(access.filespace_id), []).append({
                "agentId": str(access.agent_id),
                "agentName": access.agent.name,
            })
        for node in (
            AgentFsNode.objects.alive().filter(filespace_id__in=referenced_ids)
            .select_related("filespace")
            .order_by("filespace_id", "path", "name")
        ):
            reference = {
                "agentId": str(self.agent.id),
                "nodeId": str(node.id),
                "filespaceId": str(node.filespace_id),
                "filespaceName": node.filespace.name,
                "path": node.path or node.name,
                "nodeType": node.node_type,
                "sizeBytes": node.size_bytes,
                "sha256": node.checksum_sha256 or None,
                "ownerAgents": owners_by_space.get(str(node.filespace_id), []),
            }
            self.result.shared_file_references.append(reference)
            if node.node_type == AgentFsNode.NodeType.DIR:
                self.entries.append({
                    "id": str(node.id),
                    "category": "shared",
                    "logicalPath": node.path or node.name,
                    "archivePath": None,
                    "directory": True,
                    "filespaceId": str(node.filespace_id),
                    "filespaceName": node.filespace.name,
                    "ownerAgents": owners_by_space.get(str(node.filespace_id), []),
                })
                continue
            if not node.content or not node.content.name:
                continue
            entry = self.add_storage_file(
                storage_name=node.content.name,
                logical_path=node.path or node.name,
                category="shared",
                identifier=node.id,
                expected_sha256=node.checksum_sha256 or "",
                content_type=node.mime_type,
                size_bytes=node.size_bytes,
            )
            entry.update({
                "filespaceId": str(node.filespace_id),
                "filespaceName": node.filespace.name,
                "ownerAgents": owners_by_space.get(str(node.filespace_id), []),
                "fallbackPolicy": "private-copy-when-owner-not-imported",
            })

    def add_attachment(self, attachment) -> dict:
        if not attachment.file or not attachment.file.name:
            warning = f"Attachment {attachment.filename or attachment.id} has no stored content."
            self.result.warnings.append(warning)
            return {"id": str(attachment.id), "filename": attachment.filename, "missing": True}
        return self.add_storage_file(
            storage_name=attachment.file.name,
            logical_path=attachment.filename or f"attachment-{attachment.id}",
            category="attachments",
            identifier=attachment.id,
            expected_sha256=attachment.content_sha256 or "",
            content_type=attachment.content_type,
            size_bytes=attachment.file_size,
        )

    def write_index(self) -> None:
        write_json_file(self.agent_dir / "files/index.json", {"files": self.entries})


class PortableAgentArchiveBuilder:
    def __init__(
        self,
        agent: PersistentAgent,
        item: PortableAgentExportItem,
        destination: Path,
        *,
        content_registry: dict[str, str] | None = None,
        archive_budget: ExportArchiveBudget | None = None,
    ):
        self.agent = agent
        self.item = item
        self.destination = destination
        self.result = AgentArchiveResult()
        self.redactor = ExportRedactor(_load_known_secret_values(agent))
        self.content_registry = content_registry if content_registry is not None else {}
        self.archive_prefix = f"agents/{item.folder_name}" if content_registry is not None else ""
        self.archive_budget = archive_budget or ExportArchiveBudget(settings.PORTABLE_AGENT_EXPORT_MAX_ARCHIVE_BYTES)

    def build(self) -> AgentArchiveResult:
        self.result.display_name = self.redactor.redact_text(self.agent.name) or "Agent"
        self.destination.mkdir(parents=True, exist_ok=True)
        collector = ExportFileCollector(
            self.agent, self.destination, self.result, self.content_registry, self.archive_prefix, self.archive_budget,
        )
        collector.collect_workspace()
        self._write_identity()
        self._write_memory()
        self._write_work()
        self._write_communications()
        self._write_skills_tools_and_connections()
        self._write_history(collector)
        collector.write_index()
        self._write_sqlite()
        self._write_adapters()
        self.result.redaction_report = self.redactor.report()
        write_json_file(self.destination / "redaction-report.json", self.result.redaction_report)
        self._write_manifest()
        return self.result

    def _write_identity(self) -> None:
        owner_instructions = (
            get_custom_instructions_for_organization_id(self.agent.organization_id)
            if self.agent.organization_id
            else get_custom_instructions_for_user_id(self.agent.user_id)
        )
        tier = getattr(self.agent.preferred_llm_tier, "key", None)
        avatar_metadata = None
        profile = {
            "id": str(self.agent.id),
            "name": self.redactor.redact_text(self.agent.name),
            "shortDescription": self.redactor.redact_text(self.agent.short_description),
            "miniDescription": self.redactor.redact_text(self.agent.mini_description),
            "charter": self.redactor.redact_text(self.agent.charter),
            "visualDescription": self.redactor.redact_text(self.agent.visual_description),
            "tags": self.redactor.redact(self.agent.tags),
            "preferredIntelligenceTier": tier,
            "isActiveAtExport": self.agent.is_active,
            "lifeState": self.agent.life_state,
            "proactiveOptIn": self.agent.proactive_opt_in,
            "planningState": self.agent.planning_state,
            "createdAt": self.agent.created_at,
            "updatedAt": self.agent.updated_at,
            "ownerInstructionsSource": "organization" if self.agent.organization_id else "personal",
            "ownerInstructions": self.redactor.redact_text(owner_instructions),
            "avatar": avatar_metadata,
        }
        (self.destination / "identity").mkdir(parents=True, exist_ok=True)
        if self.agent.avatar and self.agent.avatar.name:
            suffix = os.path.splitext(self.agent.avatar.name)[1].lower()[:12] or ".bin"
            target = self.destination / f"identity/avatar{suffix}"
            try:
                with default_storage.open(self.agent.avatar.name, "rb") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except STORAGE_ERRORS as exc:
                logger.warning(
                    "Could not include portable export avatar agent=%s error=%s",
                    self.agent.id,
                    type(exc).__name__,
                )
                self.result.warnings.append("The agent avatar could not be included.")
                target.unlink(missing_ok=True)
            else:
                avatar_metadata = {
                    "archivePath": target.relative_to(self.destination).as_posix(),
                    "sourceFilename": os.path.basename(self.agent.avatar.name),
                    "sizeBytes": target.stat().st_size,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
                profile["avatar"] = avatar_metadata
        write_json_file(self.destination / "identity/profile.json", profile)
        instructions = (
            f"# {profile['name']}\n\n"
            f"## Charter\n\n{profile['charter'] or 'No charter was configured.'}\n\n"
            f"## Owner instructions\n\n{profile['ownerInstructions'] or 'No additional owner instructions were configured.'}\n"
        )
        (self.destination / "identity/instructions.md").write_text(instructions, encoding="utf-8")

    def _write_memory(self) -> None:
        comms = list(self.agent.comms_snapshots.filter(snapshot_until__lte=self.item.snapshot_at).order_by("snapshot_until", "id"))
        steps = list(self.agent.step_snapshots.filter(snapshot_until__lte=self.item.snapshot_at).order_by("snapshot_until", "id"))
        snapshots = []
        for kind, rows in (("communications", comms), ("execution", steps)):
            for row in rows:
                snapshots.append({
                    "id": str(row.id),
                    "kind": kind,
                    "snapshotUntil": row.snapshot_until,
                    "createdAt": row.created_at,
                    "summary": self.redactor.redact_text(row.summary),
                })
        memory_dir = self.destination / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        with (memory_dir / "snapshots.jsonl").open("w", encoding="utf-8") as output:
            for snapshot in snapshots:
                output.write(json.dumps(snapshot, ensure_ascii=False, default=_json_default) + "\n")
        latest_comms = comms[-1].summary if comms else "No compacted communication summary was available."
        latest_steps = steps[-1].summary if steps else "No compacted execution summary was available."
        current_state = (
            f"# Current state for {self.redactor.redact_text(self.agent.name)}\n\n"
            f"## Current plan\n\n{self.redactor.redact_text(self.agent.planning_plan) or 'No current plan was recorded.'}\n\n"
            f"## Communication memory\n\n{self.redactor.redact_text(latest_comms)}\n\n"
            f"## Execution memory\n\n{self.redactor.redact_text(latest_steps)}\n"
        )
        (memory_dir / "current-state.md").write_text(current_state, encoding="utf-8")

    def _write_work(self) -> None:
        work = self.destination / "work"
        write_json_file(work / "plan.json", {
            "state": self.agent.planning_state,
            "plan": self.redactor.redact_text(self.agent.planning_plan),
            "completedAt": self.agent.planning_completed_at,
            "deliverables": [
                {
                    "id": str(row.id), "kind": row.kind, "label": self.redactor.redact_text(row.label),
                    "path": row.path, "messageId": str(row.message_id) if row.message_id else None,
                    "position": row.position,
                }
                for row in self.agent.plan_deliverables.order_by("position", "id")
            ],
        })
        write_json_file(work / "tasks.json", {
            "tasks": [
                {
                    "id": str(card.id), "title": self.redactor.redact_text(card.title),
                    "description": self.redactor.redact_text(card.description), "status": card.status,
                    "priority": card.priority, "createdAt": card.created_at, "updatedAt": card.updated_at,
                    "completedAt": card.completed_at,
                }
                for card in self.agent.kanban_cards.order_by("priority", "created_at", "id")
            ]
        })
        schedules = []
        if self.agent.schedule:
            schedules.append({
                "id": "primary", "kind": "legacy", "expression": self.agent.schedule,
                "originalEnabled": self.agent.is_active, "enabledOnImport": False,
            })
        for schedule in self.agent.additional_schedules.order_by("schedule_key", "id"):
            schedules.append({
                "id": str(schedule.id), "key": schedule.schedule_key, "name": schedule.name,
                "instruction": self.redactor.redact_text(schedule.instruction), "kind": schedule.kind,
                "expression": schedule.expression, "timezone": schedule.timezone, "runAt": schedule.run_at,
                "originalEnabled": schedule.enabled, "enabledOnImport": False,
                "nextRunAt": schedule.next_run_at, "lastFiredAt": schedule.last_fired_at,
                "revision": schedule.revision,
            })
        write_json_file(work / "schedules.json", {"schedules": schedules, "importPolicy": "disabled"})
        pending = PersistentAgentHumanInputRequest.objects.filter(
            agent=self.agent,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
            created_at__lte=self.item.snapshot_at,
        ).order_by("created_at", "id")
        write_json_file(work / "pending-inputs.json", {
            "requests": [
                {
                    "id": str(row.id), "question": self.redactor.redact_text(row.question),
                    "options": self.redactor.redact(row.options_json), "inputMode": row.input_mode,
                    "recipientChannel": row.recipient_channel,
                    "recipientAddress": self.redactor.redact_text(row.recipient_address),
                    "createdAt": row.created_at, "expiresAt": row.expires_at,
                }
                for row in pending
            ]
        })
    def _write_communications(self) -> None:
        comms = self.destination / "communications"
        write_json_file(comms / "endpoints.json", {
            "endpoints": [
                {
                    "id": str(row.id), "channel": row.channel,
                    "address": self.redactor.redact_text(row.address), "isPrimary": row.is_primary,
                }
                for row in self.agent.comms_endpoints.order_by("channel", "address", "id")
            ]
        })
        contacts = [
            {
                "id": str(row.id), "channel": row.channel,
                "address": self.redactor.redact_text(row.address),
                "verified": row.verified, "allowInbound": row.allow_inbound,
                "allowOutbound": row.allow_outbound, "isActive": row.is_active,
            }
            for row in self.agent.manual_allowlist.order_by("channel", "address", "id")
        ]
        write_json_file(comms / "contacts.json", {"contacts": contacts})
        write_json_file(comms / "allowlist.json", {
            "policy": self.agent.whitelist_policy,
            "contactApprovalMode": self.agent.contact_approval_mode,
            "emailSendingMode": self.agent.email_sending_mode,
            "contactsFile": "contacts.json",
        })
        write_json_file(comms / "webhooks.json", {
            "outbound": [
                {
                    "id": str(row.id),
                    "name": self.redactor.redact_text(row.name),
                    "url": self.redactor.redact_text(_safe_url_without_credentials(row.url)),
                    "enabledOnImport": False,
                    "reconnectRequired": True,
                }
                for row in self.agent.webhooks.order_by("name", "id")
            ],
            "inbound": [
                {
                    "id": str(row.id),
                    "name": self.redactor.redact_text(row.name),
                    "originalEnabled": row.is_active,
                    "enabledOnImport": False,
                    "reconnectRequired": True,
                }
                for row in self.agent.inbound_webhooks.order_by("name", "id")
            ],
            "secretValuesIncluded": False,
        })
        relationships = []
        links = AgentPeerLink.objects.filter(Q(agent_a=self.agent) | Q(agent_b=self.agent)).select_related("agent_a", "agent_b")
        for link in links.order_by("created_at", "id"):
            counterpart = link.agent_b if link.agent_a_id == self.agent.id else link.agent_a
            relationships.append({
                "id": str(link.id), "counterpartAgentId": str(counterpart.id),
                "counterpartAgentName": self.redactor.redact_text(counterpart.name), "enabled": link.is_enabled,
                "messagesPerWindow": link.messages_per_window, "windowHours": link.window_hours,
            })
        write_json_file(comms / "relationships.json", {"peerAgents": relationships})

    def _write_history(self, collector: ExportFileCollector) -> None:
        history = self.destination / "history"
        history.mkdir(parents=True, exist_ok=True)
        with (
            (history / "messages.jsonl").open("w", encoding="utf-8") as message_output,
            (history / "transcript.md").open("w", encoding="utf-8") as transcript_output,
        ):
            transcript_output.write(f"# Conversation history for {self.redactor.redact_text(self.agent.name)}\n\n")
            messages = (
                visible_agent_message_queryset(self.agent)
                .filter(timestamp__lte=self.item.snapshot_at)
                .select_related("from_endpoint", "to_endpoint", "conversation", "peer_agent")
                .prefetch_related("attachments", "cc_endpoints", "bcc_endpoints")
                .order_by("timestamp", "seq")
            )
            for message in messages.iterator(chunk_size=200):
                payload = self._serialize_message(message, collector)
                message_output.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")
                sender = payload.get("sender") or {}
                author = self.redactor.redact_text(self.agent.name) if message.is_outbound else sender.get("address") or "User"
                transcript_output.write(f"## {author} — {message.timestamp.isoformat()}\n\n")
                if payload.get("subject"):
                    transcript_output.write(f"**Subject:** {payload['subject']}\n\n")
                transcript_output.write(f"{payload['body']}\n\n")
                self.result.message_count += 1

        with (
            (history / "steps.jsonl").open("w", encoding="utf-8") as step_output,
            (history / "tool-calls.jsonl").open("w", encoding="utf-8") as tool_output,
        ):
            steps = (
                visible_tool_steps_queryset(self.agent)
                .filter(created_at__lte=self.item.snapshot_at, system_step__isnull=True)
                .select_related("tool_call", "tool_call__parent_tool_call")
                .order_by("created_at", "id")
            )
            for step in steps.iterator(chunk_size=200):
                tool = step.tool_call
                step_payload = {
                    "id": str(step.id), "timestamp": step.created_at,
                    "description": self.redactor.redact_text(step.description),
                    "toolName": tool.tool_name, "status": tool.status,
                }
                result_value = "[tool error output omitted]" if tool.status == tool.Status.ERROR else self._redact_tool_result(tool.result)
                tool_payload = {
                    "stepId": str(step.id), "timestamp": step.created_at, "toolName": tool.tool_name,
                    "status": tool.status, "parameters": self.redactor.redact(tool.tool_params),
                    "result": result_value,
                    "displayMetadata": self.redactor.redact(tool.display_metadata),
                    "parentStepId": (
                        str(tool.parent_tool_call.step_id)
                        if tool.parent_tool_call_id and tool.parent_tool_call
                        else None
                    ),
                }
                step_output.write(json.dumps(step_payload, ensure_ascii=False, default=_json_default) + "\n")
                tool_output.write(json.dumps(tool_payload, ensure_ascii=False, default=_json_default) + "\n")
                self.result.step_count += 1

    def _serialize_message(self, message: PersistentAgentMessage, collector: ExportFileCollector) -> dict:
        channel = (
            message.conversation.channel if message.conversation_id
            else message.from_endpoint.channel if message.from_endpoint_id
            else "other"
        )
        subject = ""
        raw = message.raw_payload if isinstance(message.raw_payload, dict) else {}
        for key in ("subject", "Subject"):
            if isinstance(raw.get(key), str):
                subject = raw[key]
                break
        if not subject and isinstance(raw.get("headers"), dict):
            for key, value in raw["headers"].items():
                if str(key).lower() == "subject" and isinstance(value, str):
                    subject = value
                    break
        attachments = [collector.add_attachment(row) for row in message.attachments.all()]
        return {
            "id": str(message.id), "sequence": message.seq, "timestamp": message.timestamp,
            "direction": "outbound" if message.is_outbound else "inbound", "channel": channel,
            "subject": self.redactor.redact_text(subject) or None,
            "body": self.redactor.redact_text(message.body),
            "sender": self._serialize_endpoint(message.from_endpoint),
            "recipient": self._serialize_endpoint(message.to_endpoint),
            "cc": [self._serialize_endpoint(row) for row in message.cc_endpoints.all()],
            "bcc": [self._serialize_endpoint(row) for row in message.bcc_endpoints.all()],
            "conversation": {
                "id": str(message.conversation_id),
                "address": self.redactor.redact_text(message.conversation.address),
                "displayName": self.redactor.redact_text(message.conversation.display_name),
                "isPeerConversation": message.conversation.is_peer_dm,
            } if message.conversation_id else None,
            "parentMessageId": str(message.parent_id) if message.parent_id else None,
            "peerAgent": {
                "id": str(message.peer_agent_id), "name": message.peer_agent.name,
            } if message.peer_agent_id and message.peer_agent else None,
            "deliveryStatus": message.latest_status,
            "attachments": attachments,
        }

    def _serialize_endpoint(self, endpoint) -> dict | None:
        if endpoint is None:
            return None
        return {
            "id": str(endpoint.id),
            "channel": endpoint.channel,
            "address": self.redactor.redact_text(endpoint.address),
        }

    def _redact_tool_result(self, result: str):
        if not result:
            return ""
        try:
            decoded = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return self.redactor.redact_text(result)
        return self.redactor.redact(decoded)

    def _write_sqlite(self) -> None:
        state_dir = self.destination / "state/sqlite"
        state_dir.mkdir(parents=True, exist_ok=True)
        db_path = state_dir / "state.sqlite3"
        try:
            write_agent_sqlite_export_snapshot(str(self.agent.id), str(db_path))
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                schema_rows = connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
                ).fetchall()
                tables = []
                for (table_name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall():
                    columns = [
                        {"name": row[1], "type": row[2], "notNull": bool(row[3]), "default": row[4], "primaryKey": bool(row[5])}
                        for row in connection.execute(f"PRAGMA table_info({_quote_sqlite_identifier(table_name)})").fetchall()
                    ]
                    row_count = connection.execute(
                        f"SELECT COUNT(*) FROM {_quote_sqlite_identifier(table_name)}"
                    ).fetchone()[0]
                    tables.append({"name": table_name, "columns": columns, "rowCount": row_count})
            finally:
                connection.close()
            schema_sql = "\n\n".join(row[3].rstrip(";") + ";" for row in schema_rows) + "\n"
            (state_dir / "schema.sql").write_text(schema_sql, encoding="utf-8")
            write_json_file(state_dir / "tables.json", {"tables": tables})
        except SQLITE_EXPORT_ERRORS as exc:
            logger.warning(
                "Could not include portable SQLite state agent=%s error=%s",
                self.agent.id,
                type(exc).__name__,
            )
            db_path.unlink(missing_ok=True)
            self.result.warnings.append("SQLite state could not be included.")
            write_json_file(state_dir / "tables.json", {"tables": [], "unavailable": True})
            (state_dir / "schema.sql").write_text("-- SQLite state was unavailable during export.\n", encoding="utf-8")

    def _write_skills_tools_and_connections(self) -> None:
        skills_dir = self.destination / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        latest_skills = get_latest_skill_versions(self.agent)
        skill_index = []
        for skill in latest_skills:
            skill_name = self.redactor.redact_text(skill.name) or "Skill"
            skill_description = self.redactor.redact_text(skill.description or "")
            folder = skills_dir / (_safe_segment(slugify(skill_name), "skill") + f"--v{skill.version}")
            folder.mkdir(parents=True, exist_ok=True)
            tools = [str(value) for value in (skill.tools or [])]
            secrets = [str(value) for value in (skill.secrets or [])]
            body = (
                "---\n"
                f"name: {json.dumps(skill_name)}\n"
                f"description: {json.dumps(skill_description)}\n"
                f"version: {skill.version}\n"
                "---\n\n"
                f"# {skill_name}\n\n"
                f"{self.redactor.redact_text(skill.instructions)}\n\n"
                f"## Required tools\n\n{chr(10).join(f'- `{self.redactor.redact_text(value)}`' for value in tools) or '- None'}\n\n"
                f"## Required connections\n\n{chr(10).join(f'- `{self.redactor.redact_text(value)}`' for value in secrets) or '- None'}\n"
            )
            (folder / "SKILL.md").write_text(body, encoding="utf-8")
            skill_index.append({
                "name": skill_name,
                "description": skill_description,
                "version": skill.version,
                "tools": tools,
                "secrets": secrets,
                "instructions": self.redactor.redact_text(skill.instructions),
                "archivePath": (folder / "SKILL.md").relative_to(self.destination).as_posix(),
            })
        write_json_file(skills_dir / "index.json", {"skills": skill_index})

        system_skills = []
        for state in self.agent.system_skill_states.filter(is_enabled=True).order_by("skill_key"):
            definition = get_system_skill_definition(state.skill_key)
            if definition is None:
                system_skills.append({"key": state.skill_key, "definitionAvailable": False})
                continue
            system_skills.append({
                "key": definition.skill_key, "name": definition.name,
                "toolNames": list(definition.tool_names),
                "setupInstructions": self.redactor.redact_text(definition.setup_instructions),
                "setupSteps": self.redactor.redact(list(definition.setup_steps)),
                "definitionAvailable": True, "portability": "reconnect-required",
            })

        custom_tools = []
        for tool in self.agent.custom_tools.order_by("tool_name", "id"):
            custom_tool = {
                "id": str(tool.id), "name": tool.name, "toolName": tool.tool_name,
                "description": self.redactor.redact_text(tool.description),
                "parametersSchema": self.redactor.redact(tool.parameters_schema),
                "entrypoint": tool.entrypoint, "timeoutSeconds": tool.timeout_seconds,
                "sourcePath": tool.source_path, "sourceArchivePath": None,
                "enabledAtExport": self.agent.enabled_tools.filter(tool_full_name=tool.tool_name).exists(),
                "enabledOnImport": False, "portability": "portable-disabled",
            }
            custom_tools.append(custom_tool)
            node = AgentFsNode.objects.alive().files().filter(
                filespace__access__agent=self.agent,
                path=tool.source_path,
            ).exclude(content="").first()
            if node and node.content and node.content.name:
                try:
                    with default_storage.open(node.content.name, "rb") as source:
                        text = source.read().decode("utf-8")
                    target = self.destination / "tools/custom" / f"{_safe_segment(tool.tool_name, 'tool')}.py"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(self.redactor.redact_text(text), encoding="utf-8")
                    custom_tool["sourceArchivePath"] = target.relative_to(self.destination).as_posix()
                except STORAGE_ERRORS + (UnicodeDecodeError,) as exc:
                    logger.warning(
                        "Could not include custom tool source agent=%s tool=%s error=%s",
                        self.agent.id,
                        tool.id,
                        type(exc).__name__,
                    )
                    self.result.warnings.append("A custom tool source file could not be included.")
            else:
                self.result.warnings.append(f"Custom tool source {tool.source_path} was not found.")

        server_configs = agent_accessible_server_configs(self.agent)
        servers = []
        for config in server_configs:
            metadata = config.metadata if isinstance(config.metadata, Mapping) else {}
            servers.append({
                "id": str(config.id), "name": self.redactor.redact_text(config.name),
                "displayName": self.redactor.redact_text(config.display_name),
                "description": self.redactor.redact_text(config.description), "scope": config.scope,
                "transport": config.transport, "command": self.redactor.redact_text(config.command),
                "commandArgs": self.redactor.redact(config.command_args),
                "url": self.redactor.redact_text(_safe_url_without_credentials(config.url)),
                "authMethod": config.auth_method,
                "managedIntegrationKey": config.managed_integration_key or None,
                "requiredScopes": self.redactor.redact(metadata.get("scopes", [])),
                "portability": "reconnect-required",
            })
        write_json_file(self.destination / "tools/mcp-servers.json", {"servers": servers})
        enabled_tools = [
            {
                "id": str(row.id), "fullName": row.tool_full_name, "server": row.tool_server,
                "name": row.tool_name, "serverConfigId": str(row.server_config_id) if row.server_config_id else None,
                "type": "mcp" if row.server_config_id else "builtin",
                "portability": "reconnect-required" if row.server_config_id else "unsupported",
            }
            for row in self.agent.enabled_tools.order_by("tool_full_name", "id")
        ]
        write_json_file(self.destination / "tools/capabilities.json", {
            "enabledTools": enabled_tools, "customTools": custom_tools, "systemSkills": system_skills,
            "importPolicy": {
                "builtInTools": "restore-when-supported",
                "systemSkills": "restore-when-supported",
                "customTools": "restore-source-disabled",
                "mcpTools": "record-reconnection-required",
            },
        })

        secret_requirements = []
        for secret in self.agent.secrets.order_by("secret_type", "domain_pattern", "name"):
            secret_requirements.append({
                "scope": "agent", "name": self.redactor.redact_text(secret.name),
                "key": self.redactor.redact_text(secret.key),
                "type": secret.secret_type, "domain": secret.domain_pattern,
                "description": self.redactor.redact_text(secret.description), "requested": secret.requested,
                "requiredScopes": [],
            })
        global_filter = (
            Q(organization_id=self.agent.organization_id)
            if self.agent.organization_id else Q(user_id=self.agent.user_id, organization__isnull=True)
        )
        for secret in GlobalSecret.objects.filter(global_filter).order_by("secret_type", "domain_pattern", "name"):
            secret_requirements.append({
                "scope": "organization" if secret.organization_id else "personal",
                "name": self.redactor.redact_text(secret.name),
                "key": self.redactor.redact_text(secret.key), "type": secret.secret_type,
                "domain": secret.domain_pattern, "description": self.redactor.redact_text(secret.description),
                "requiredScopes": [],
            })
        write_json_file(self.destination / "connections/requirements.json", {
            "credentialsIncluded": False, "secretRequirements": secret_requirements,
            "mcpServersFile": "../tools/mcp-servers.json",
        })
        connections_readme = (
            "# Reconnect services\n\n"
            "Credential values are intentionally not present in this export. Reconnect each integration and MCP server, "
            "then provide the secret names listed in `requirements.json`. Review scopes before enabling writes.\n"
        )
        (self.destination / "connections").mkdir(parents=True, exist_ok=True)
        (self.destination / "connections/README.md").write_text(connections_readme, encoding="utf-8")

    def _write_adapters(self) -> None:
        adapters = {
            "hermes": (
                "Copy the ready-to-use folders under `../../skills/` into your Hermes skills directory. Use "
                "`../../identity/instructions.md` as the agent system instructions and `../../memory/current-state.md` "
                "as initial memory. Reconnect every requirement first."
            ),
            "manus": (
                "Upload or copy the ready-to-use folders under `../../skills/` as Manus skills. Add "
                "`../../identity/instructions.md`, current memory, and selected files as project context. Recreate schedules "
                "only after reviewing them."
            ),
            "chatgpt": (
                "Use `../../identity/instructions.md` as GPT instructions. Add `../../memory/current-state.md`, the transcript, and selected "
                "workspace files as knowledge. Recreate compatible actions from `../../tools/capabilities.json`; this is not a one-click GPT import."
            ),
            "gemini": (
                "Use `../../identity/instructions.md` and `../../memory/current-state.md` as initial instructions and memory. Add the transcript and "
                "selected workspace files as context. Reconnect tools manually; this is not a one-click Gemini import."
            ),
            "gobii": (
                "Use New agent → Import agent and upload the complete ZIP. Gobii validates every checksum, lets you "
                "select and rename agents, and restores compatible state. Schedules, proactive work, external channels, "
                "MCP connections, webhooks, and custom tools stay disabled until reviewed."
            ),
        }
        for name, body in adapters.items():
            folder = self.destination / "adapters" / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "README.md").write_text(f"# Import into {name.title()}\n\n{body}\n", encoding="utf-8")

    def _write_manifest(self) -> None:
        write_json_file(self.destination / "manifest.json", {
            "formatVersion": self.item.export.format_version,
            "agentId": str(self.agent.id), "agentName": self.redactor.redact_text(self.agent.name),
            "snapshotAt": self.item.snapshot_at, "folderName": self.item.folder_name,
            "counts": {
                "messages": self.result.message_count, "steps": self.result.step_count,
                "files": self.result.file_count, "warnings": len(self.result.warnings),
            },
            "warnings": self.result.warnings,
            "excluded": [
                "managed credential values", "prompt archives", "hidden reasoning", "provider errors and tracebacks",
                "billing and model routing", "live compute and browser sessions", "task leases",
            ],
        })
        readme = (
            f"# Portable export for {self.redactor.redact_text(self.agent.name)}\n\n"
            "Start with `identity/instructions.md`, `memory/current-state.md`, and the destination guide under `adapters/`. "
            "The SQLite database and files may contain sensitive user-provided content. Credentials are not included. "
            "Schedules are exported for reference and must be deliberately re-enabled.\n"
        )
        (self.destination / "README.md").write_text(readme, encoding="utf-8")
