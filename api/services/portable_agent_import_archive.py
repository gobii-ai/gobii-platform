import hashlib
import json
import re
import stat
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterator

from django.conf import settings
from django.utils.dateparse import parse_datetime


FORMAT_V1 = "gobii.agent-portable-export/v1"
FORMAT_V2 = "gobii.agent-portable-export/v2"
SUPPORTED_FORMATS = frozenset({FORMAT_V1, FORMAT_V2})
CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})  (.+)$")
MAX_METADATA_BYTES = 5 * 1024 * 1024


class PortableAgentImportArchiveError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_archive"):
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class PortableAgentImportCandidate:
    source_agent_id: uuid.UUID
    source_agent_name: str
    folder_name: str
    snapshot_at: object | None
    selectable: bool
    message_count: int = 0
    step_count: int = 0
    file_count: int = 0
    warnings: list[str] = field(default_factory=list)
    compatibility: dict = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class PortableAgentImportValidation:
    format_version: str
    candidates: list[PortableAgentImportCandidate]


def _safe_archive_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path or "\x00" in raw_path:
        raise PortableAgentImportArchiveError("The archive contains an unsafe file path.", code="unsafe_path")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PortableAgentImportArchiveError("The archive contains an unsafe file path.", code="unsafe_path")
    return path.as_posix()


def _read_member_bytes(archive: zipfile.ZipFile, name: str, *, limit: int = MAX_METADATA_BYTES) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise PortableAgentImportArchiveError(f"The archive is missing {name}.", code="missing_file") from exc
    if info.file_size > limit:
        raise PortableAgentImportArchiveError(f"Archive metadata file {name} is too large.", code="metadata_too_large")
    with archive.open(info, "r") as source:
        payload = source.read(limit + 1)
    if len(payload) > limit:
        raise PortableAgentImportArchiveError(f"Archive metadata file {name} is too large.", code="metadata_too_large")
    return payload


def _read_json(archive: zipfile.ZipFile, name: str) -> dict:
    try:
        value = json.loads(_read_member_bytes(archive, name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableAgentImportArchiveError(f"Archive metadata file {name} is invalid JSON.", code="invalid_json") from exc
    if not isinstance(value, dict):
        raise PortableAgentImportArchiveError(f"Archive metadata file {name} must contain an object.", code="invalid_json")
    return value


def _validate_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > settings.PORTABLE_AGENT_IMPORT_MAX_ENTRIES:
        raise PortableAgentImportArchiveError("The archive contains too many files.", code="too_many_entries")

    members: dict[str, zipfile.ZipInfo] = {}
    normalized_names: set[str] = set()
    total_uncompressed = 0
    for info in infos:
        name = _safe_archive_path(info.filename.rstrip("/"))
        normalized = name.casefold()
        if normalized in normalized_names:
            raise PortableAgentImportArchiveError("The archive contains duplicate file paths.", code="duplicate_path")
        normalized_names.add(normalized)
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise PortableAgentImportArchiveError("Symbolic links are not allowed in agent imports.", code="symlink")
        if info.flag_bits & 0x1:
            raise PortableAgentImportArchiveError("Encrypted ZIP entries are not supported.", code="encrypted_archive")
        if info.is_dir():
            continue
        total_uncompressed += info.file_size
        if total_uncompressed > settings.PORTABLE_AGENT_IMPORT_MAX_ARCHIVE_BYTES:
            raise PortableAgentImportArchiveError("The archive expands beyond the import size limit.", code="archive_too_large")
        members[name] = info
    return members


def _parse_checksums(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        text = _read_member_bytes(archive, "checksums.sha256", limit=MAX_METADATA_BYTES * 4).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PortableAgentImportArchiveError("checksums.sha256 is not valid UTF-8.", code="invalid_checksums") from exc
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        match = CHECKSUM_RE.fullmatch(line)
        if match is None:
            raise PortableAgentImportArchiveError("checksums.sha256 is malformed.", code="invalid_checksums")
        path = _safe_archive_path(match.group(2))
        if path in checksums:
            raise PortableAgentImportArchiveError("checksums.sha256 contains duplicate paths.", code="invalid_checksums")
        checksums[path] = match.group(1)
    return checksums


def _verify_checksums(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    checksums: dict[str, str],
) -> None:
    expected_paths = set(members) - {"checksums.sha256"}
    if set(checksums) != expected_paths:
        raise PortableAgentImportArchiveError(
            "The archive checksum index does not match its files.",
            code="invalid_checksums",
        )
    for name in sorted(expected_paths):
        digest = hashlib.sha256()
        with archive.open(members[name], "r") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != checksums[name]:
            raise PortableAgentImportArchiveError(
                f"Checksum verification failed for {name}.",
                code="checksum_mismatch",
            )


def _folder_name(folder: str) -> str:
    safe = _safe_archive_path(folder)
    parts = PurePosixPath(safe).parts
    if len(parts) != 2 or parts[0] != "agents":
        raise PortableAgentImportArchiveError("An agent folder is outside agents/.", code="invalid_manifest")
    return parts[1]


def _positive_count(value) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _validate_declared_agent_files(
    archive: zipfile.ZipFile,
    *,
    folder_name: str,
    profile: dict,
    checksums: dict[str, str],
) -> None:
    prefix = f"agents/{folder_name}"
    avatar = profile.get("avatar")
    if isinstance(avatar, dict) and avatar.get("archivePath"):
        relative = _safe_archive_path(str(avatar["archivePath"]))
        path = f"{prefix}/{relative}"
        if path not in checksums or (avatar.get("sha256") and avatar["sha256"] != checksums[path]):
            raise PortableAgentImportArchiveError("The avatar restoration metadata is inconsistent.", code="checksum_mismatch")

    index_name = f"{prefix}/files/index.json"
    if index_name not in checksums:
        return
    index = _read_json(archive, index_name)
    rows = index.get("files")
    if not isinstance(rows, list):
        raise PortableAgentImportArchiveError("The agent file index is malformed.", code="invalid_manifest")
    for row in rows:
        if not isinstance(row, dict):
            raise PortableAgentImportArchiveError("The agent file index is malformed.", code="invalid_manifest")
        archive_path = row.get("archivePath")
        if not archive_path:
            continue
        safe_path = _safe_archive_path(str(archive_path))
        resolved = safe_path if row.get("archivePathScope") == "bundle" else f"{prefix}/{safe_path}"
        if resolved not in checksums:
            raise PortableAgentImportArchiveError("A declared agent file is missing.", code="missing_file")
        declared_sha = row.get("sha256")
        if declared_sha and declared_sha != checksums[resolved]:
            raise PortableAgentImportArchiveError("A declared agent file checksum is invalid.", code="checksum_mismatch")


def _validate_v2_metadata(archive: zipfile.ZipFile, folder_name: str) -> None:
    prefix = f"agents/{folder_name}"
    filespaces = _read_json(archive, f"{prefix}/files/filespaces.json").get("filespaces")
    skills = _read_json(archive, f"{prefix}/skills/index.json").get("skills")
    capabilities = _read_json(archive, f"{prefix}/tools/capabilities.json")
    if not isinstance(filespaces, list) or not all(isinstance(row, dict) for row in filespaces):
        raise PortableAgentImportArchiveError("The v2 filespace metadata is malformed.", code="invalid_manifest")
    for row in filespaces:
        if not row.get("sourceFilespaceId") or row.get("role") not in {"OWNER", "WRITER", "READER"}:
            raise PortableAgentImportArchiveError("The v2 filespace metadata is malformed.", code="invalid_manifest")
        try:
            uuid.UUID(str(row["sourceFilespaceId"]))
        except ValueError as exc:
            raise PortableAgentImportArchiveError("The v2 filespace metadata is malformed.", code="invalid_manifest") from exc
        agent_access = row.get("agentAccess")
        if not isinstance(agent_access, list) or not all(isinstance(access, dict) for access in agent_access):
            raise PortableAgentImportArchiveError("The v2 filespace sharing metadata is malformed.", code="invalid_manifest")
        for access in agent_access:
            try:
                uuid.UUID(str(access.get("agentId") or ""))
            except ValueError as exc:
                raise PortableAgentImportArchiveError("The v2 filespace sharing metadata is malformed.", code="invalid_manifest") from exc
            if access.get("role") not in {"OWNER", "WRITER", "READER"}:
                raise PortableAgentImportArchiveError("The v2 filespace sharing metadata is malformed.", code="invalid_manifest")
    if not isinstance(skills, list) or not all(isinstance(row, dict) for row in skills):
        raise PortableAgentImportArchiveError("The v2 skill metadata is malformed.", code="invalid_manifest")
    for key in ("enabledTools", "customTools", "systemSkills"):
        rows = capabilities.get(key)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise PortableAgentImportArchiveError("The v2 capability metadata is malformed.", code="invalid_manifest")


def validate_portable_agent_archive(path: str | Path) -> PortableAgentImportValidation:
    try:
        if Path(path).stat().st_size > settings.PORTABLE_AGENT_IMPORT_MAX_ARCHIVE_BYTES:
            raise PortableAgentImportArchiveError(
                "The ZIP exceeds the portable-agent import size limit.",
                code="archive_too_large",
            )
    except OSError as exc:
        raise PortableAgentImportArchiveError("The uploaded ZIP could not be read.", code="bad_zip") from exc
    try:
        archive = zipfile.ZipFile(path, "r", allowZip64=True)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PortableAgentImportArchiveError("Choose a valid Gobii portable-export ZIP.", code="bad_zip") from exc

    with archive:
        members = _validate_members(archive)
        for required in ("manifest.json", "checksums.sha256"):
            if required not in members:
                raise PortableAgentImportArchiveError(f"The archive is missing {required}.", code="missing_file")
        checksums = _parse_checksums(archive)
        _verify_checksums(archive, members, checksums)
        manifest = _read_json(archive, "manifest.json")
        format_version = str(manifest.get("formatVersion") or "")
        if format_version not in SUPPORTED_FORMATS:
            if format_version.startswith("gobii.agent-portable-export/"):
                message = "This export was created by a newer Gobii version. Upgrade this installation before importing it."
                code = "unsupported_version"
            else:
                message = "This ZIP is not a supported Gobii portable-agent export."
                code = "invalid_format"
            raise PortableAgentImportArchiveError(message, code=code)

        raw_agents = manifest.get("agents")
        if not isinstance(raw_agents, list) or not raw_agents:
            raise PortableAgentImportArchiveError("The export manifest contains no agents.", code="invalid_manifest")

        candidates: list[PortableAgentImportCandidate] = []
        seen_ids: set[uuid.UUID] = set()
        seen_folders: set[str] = set()
        for raw in raw_agents:
            if not isinstance(raw, dict):
                raise PortableAgentImportArchiveError("The export agent list is malformed.", code="invalid_manifest")
            try:
                source_id = uuid.UUID(str(raw.get("id") or ""))
            except ValueError as exc:
                raise PortableAgentImportArchiveError("An exported agent ID is invalid.", code="invalid_manifest") from exc
            if source_id in seen_ids:
                raise PortableAgentImportArchiveError("The export contains a duplicate agent ID.", code="invalid_manifest")
            seen_ids.add(source_id)
            status = str(raw.get("status") or "")
            folder_value = raw.get("folder")
            folder_name = ""
            selectable = status == "ready" and isinstance(folder_value, str) and bool(folder_value)
            agent_manifest: dict = {}
            profile: dict = {}
            warnings: list[str] = []
            compatibility: dict = {"formatVersion": format_version}
            error = str(raw.get("error") or "")
            if selectable:
                folder_name = _folder_name(folder_value)
                if folder_name.casefold() in seen_folders:
                    raise PortableAgentImportArchiveError("The export contains a duplicate agent folder.", code="invalid_manifest")
                seen_folders.add(folder_name.casefold())
                required_paths = [
                    f"agents/{folder_name}/manifest.json",
                    f"agents/{folder_name}/identity/profile.json",
                ]
                if any(name not in members for name in required_paths):
                    selectable = False
                    error = "Required agent metadata is missing from this archive."
                else:
                    agent_manifest = _read_json(archive, required_paths[0])
                    profile = _read_json(archive, required_paths[1])
                    if str(agent_manifest.get("agentId") or "") != str(source_id):
                        raise PortableAgentImportArchiveError("An agent manifest ID does not match the bundle manifest.", code="invalid_manifest")
                    if str(agent_manifest.get("formatVersion") or "") != format_version:
                        raise PortableAgentImportArchiveError("An agent manifest format does not match the bundle manifest.", code="invalid_manifest")
                    if format_version == FORMAT_V1:
                        if f"agents/{folder_name}/files/filespaces.json" not in members:
                            warnings.append("This v1 export does not include exact filespace access metadata; files will use a compatible fallback layout.")
                            compatibility["filespaces"] = "best_effort"
                        if f"agents/{folder_name}/skills/index.json" not in members:
                            warnings.append("This v1 export does not include a skill index; skills will be read from their SKILL.md files.")
                            compatibility["skills"] = "best_effort"
                    else:
                        v2_required = [
                            f"agents/{folder_name}/files/filespaces.json",
                            f"agents/{folder_name}/skills/index.json",
                            f"agents/{folder_name}/tools/capabilities.json",
                        ]
                        if any(name not in members for name in v2_required):
                            raise PortableAgentImportArchiveError(
                                "The v2 agent restoration metadata is incomplete.",
                                code="invalid_manifest",
                            )
                        _validate_v2_metadata(archive, folder_name)
                    _validate_declared_agent_files(
                        archive,
                        folder_name=folder_name,
                        profile=profile,
                        checksums=checksums,
                    )
            counts = agent_manifest.get("counts") if isinstance(agent_manifest.get("counts"), dict) else {}
            name = str(profile.get("name") or raw.get("name") or "Agent").strip()[:255] or "Agent"
            candidates.append(PortableAgentImportCandidate(
                source_agent_id=source_id,
                source_agent_name=name,
                folder_name=folder_name or f"unavailable-{str(source_id).replace('-', '')[:8]}",
                snapshot_at=parse_datetime(str(raw.get("snapshotAt") or "")) or None,
                selectable=selectable,
                message_count=_positive_count(counts.get("messages")),
                step_count=_positive_count(counts.get("steps")),
                file_count=_positive_count(counts.get("files")),
                warnings=warnings,
                compatibility=compatibility,
                error=error or ("This agent was not successfully exported." if not selectable else ""),
            ))
        if not any(candidate.selectable for candidate in candidates):
            raise PortableAgentImportArchiveError("The archive contains no importable agents.", code="no_importable_agents")
        return PortableAgentImportValidation(format_version=format_version, candidates=candidates)


class PortableAgentImportArchive:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.archive = zipfile.ZipFile(self.path, "r", allowZip64=True)

    def close(self) -> None:
        self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def has(self, name: str) -> bool:
        return _safe_archive_path(name) in self.archive.namelist()

    def json(self, name: str) -> dict:
        return _read_json(self.archive, _safe_archive_path(name))

    def bytes(self, name: str, *, limit: int | None = None) -> bytes:
        safe_name = _safe_archive_path(name)
        info = self.archive.getinfo(safe_name)
        if limit is not None and info.file_size > limit:
            raise PortableAgentImportArchiveError(f"{safe_name} exceeds the destination file limit.", code="file_too_large")
        with self.archive.open(info, "r") as source:
            payload = source.read((limit + 1) if limit is not None else -1)
        if limit is not None and len(payload) > limit:
            raise PortableAgentImportArchiveError(f"{safe_name} exceeds the destination file limit.", code="file_too_large")
        return payload

    def iter_jsonl(self, name: str) -> Iterator[dict]:
        safe_name = _safe_archive_path(name)
        try:
            info = self.archive.getinfo(safe_name)
        except KeyError:
            return
        with self.archive.open(info, "r") as source:
            line_number = 0
            while True:
                raw_line = source.readline(MAX_METADATA_BYTES + 1)
                if not raw_line:
                    break
                line_number += 1
                if len(raw_line) > MAX_METADATA_BYTES:
                    raise PortableAgentImportArchiveError(
                        f"{safe_name} contains an oversized JSON record on line {line_number}.",
                        code="metadata_too_large",
                    )
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise PortableAgentImportArchiveError(
                        f"{safe_name} contains invalid JSON on line {line_number}.",
                        code="invalid_jsonl",
                    ) from exc
                if not isinstance(value, dict):
                    raise PortableAgentImportArchiveError(
                        f"{safe_name} contains a non-object record.",
                        code="invalid_jsonl",
                    )
                yield value

    def names_under(self, prefix: str) -> list[str]:
        safe_prefix = _safe_archive_path(prefix).rstrip("/") + "/"
        return sorted(name for name in self.archive.namelist() if name.startswith(safe_prefix) and not name.endswith("/"))
