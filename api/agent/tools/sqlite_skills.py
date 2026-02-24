"""
SQLite-backed agent skill helpers.

Seeds an ephemeral skills table for each LLM invocation and applies updates
back to Postgres after tool execution.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional, Sequence

from django.db import transaction

from api.models import PersistentAgentSkill

from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import AGENT_SKILLS_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentSkillSnapshotRow:
    skill_id: str
    name: str
    description: str
    tools: tuple[str, ...]
    instructions: str


@dataclass(frozen=True)
class AgentSkillsSnapshot:
    by_id: dict[str, AgentSkillSnapshotRow]
    names: frozenset[str]


@dataclass(frozen=True)
class AgentSkillsApplyResult:
    created_versions: Sequence[str]
    deleted_names: Sequence[str]
    errors: Sequence[str] = ()
    changed: bool = False


@dataclass(frozen=True)
class _SQLiteSkillRow:
    skill_id: str
    name: str
    description: str
    tools: tuple[str, ...]
    instructions: str


def seed_sqlite_skills(agent) -> Optional[AgentSkillsSnapshot]:
    """Create/reset the skills table and seed it with all stored versions."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; cannot seed skills table.")
        return None

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        conn.execute(f'DROP TABLE IF EXISTS "{AGENT_SKILLS_TABLE}";')
        conn.execute(
            f"""
            CREATE TABLE "{AGENT_SKILLS_TABLE}" (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                name TEXT NOT NULL,
                description TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                tools TEXT NOT NULL DEFAULT '[]',
                instructions TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )

        skills = list(
            PersistentAgentSkill.objects.filter(agent=agent).order_by("name", "version")
        )
        rows = []
        snapshot_rows: dict[str, AgentSkillSnapshotRow] = {}
        for skill in skills:
            skill_id = str(skill.id)
            name = (skill.name or "").strip()
            description = (skill.description or "").strip()
            version = int(skill.version or 0)
            tools = _normalize_tools_sequence(skill.tools)
            instructions = (skill.instructions or "").strip()
            rows.append(
                (
                    skill_id,
                    name,
                    description,
                    version,
                    json.dumps(list(tools)),
                    instructions,
                    _format_timestamp(skill.created_at),
                    _format_timestamp(skill.updated_at),
                )
            )
            snapshot_rows[skill_id] = AgentSkillSnapshotRow(
                skill_id=skill_id,
                name=name,
                description=description,
                tools=tools,
                instructions=instructions,
            )

        if rows:
            conn.executemany(
                f"""
                INSERT INTO "{AGENT_SKILLS_TABLE}"
                    (id, name, description, version, tools, instructions, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )
        conn.commit()
        return AgentSkillsSnapshot(
            by_id=snapshot_rows,
            names=frozenset(row.name for row in snapshot_rows.values()),
        )
    except sqlite3.Error:
        logger.exception("Failed to seed skills table for agent %s", getattr(agent, "id", None))
        return None
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                logger.debug("Failed closing SQLite connection after skill seeding", exc_info=True)


def apply_sqlite_skill_updates(agent, baseline: Optional[AgentSkillsSnapshot]) -> AgentSkillsApplyResult:
    """Apply SQLite skills updates to persistent skill versions."""
    created_versions: list[str] = []
    deleted_names: list[str] = []
    errors: list[str] = []

    current_rows, read_errors = _read_sqlite_skills()
    if read_errors:
        errors.extend(read_errors)

    if baseline is None or current_rows is None:
        _drop_skill_table()
        return AgentSkillsApplyResult(
            created_versions=created_versions,
            deleted_names=deleted_names,
            errors=errors,
            changed=False,
        )

    current_names = {row.name for row in current_rows}
    deleted_names = sorted(name for name in baseline.names if name not in current_names)

    candidates_by_name: dict[str, _SQLiteSkillRow] = {}
    for row in current_rows:
        baseline_row = baseline.by_id.get(row.skill_id)
        if baseline_row and _rows_match_snapshot(row, baseline_row):
            continue
        candidates_by_name[row.name] = row

    from .tool_manager import get_available_tool_ids

    with transaction.atomic():
        if deleted_names:
            PersistentAgentSkill.objects.filter(
                agent=agent,
                name__in=deleted_names,
            ).delete()

        valid_tool_ids = get_available_tool_ids(agent)

        for name, row in candidates_by_name.items():
            unknown = [tool_id for tool_id in row.tools if tool_id not in valid_tool_ids]
            if unknown:
                errors.append(
                    f"Skill '{name}' rejected: unknown canonical tool id(s): {', '.join(unknown)}"
                )
                continue

            latest = (
                PersistentAgentSkill.objects.filter(agent=agent, name=name)
                .order_by("-version", "-updated_at")
                .first()
            )
            if latest and _is_same_skill_content(latest, row):
                continue

            next_version = (latest.version if latest else 0) + 1
            PersistentAgentSkill.objects.create(
                agent=agent,
                name=name,
                description=row.description,
                version=next_version,
                tools=list(row.tools),
                instructions=row.instructions,
            )
            created_versions.append(f"{name}@{next_version}")

    _drop_skill_table()
    return AgentSkillsApplyResult(
        created_versions=created_versions,
        deleted_names=deleted_names,
        errors=errors,
        changed=bool(created_versions or deleted_names),
    )


def get_latest_skill_versions(agent) -> list[PersistentAgentSkill]:
    """Return latest version rows per skill name for an agent."""
    rows = list(
        PersistentAgentSkill.objects.filter(agent=agent)
        .order_by("name", "-version", "-updated_at")
    )
    latest_by_name: dict[str, PersistentAgentSkill] = {}
    for row in rows:
        if row.name not in latest_by_name:
            latest_by_name[row.name] = row

    return sorted(
        latest_by_name.values(),
        key=lambda row: row.updated_at,
        reverse=True,
    )


def get_required_skill_tool_ids(agent) -> set[str]:
    """Return the union of canonical tool IDs required by latest skill versions."""
    required: set[str] = set()
    for skill in get_latest_skill_versions(agent):
        for tool_id in _normalize_tools_sequence(skill.tools):
            required.add(tool_id)
    return required


def format_recent_skills_for_prompt(agent, limit: int = 3) -> str:
    """Format top-N recently updated skills for a high-priority prompt section."""
    if limit <= 0:
        return ""

    latest = get_latest_skill_versions(agent)[:limit]
    if not latest:
        return ""

    sections: list[str] = []
    for skill in latest:
        tools = _normalize_tools_sequence(skill.tools)
        tool_text = ", ".join(tools) if tools else "(none)"
        description = (skill.description or "").strip() or "(no description)"
        instructions = (skill.instructions or "").strip()
        if not instructions:
            instructions = "(no instructions)"
        sections.append(
            "\n".join(
                [
                    f"Skill: {skill.name} (v{skill.version})",
                    f"Description: {description}",
                    f"Tools: {tool_text}",
                    "Instructions:",
                    instructions,
                ]
            )
        )

    return "\n\n".join(sections)


def _read_sqlite_skills() -> tuple[Optional[list[_SQLiteSkillRow]], list[str]]:
    db_path = get_sqlite_db_path()
    if not db_path:
        return None, ["SQLite DB path unavailable; cannot read skills table."]

    conn = None
    errors: list[str] = []
    rows: list[_SQLiteSkillRow] = []

    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, name, description, tools, instructions
            FROM "{AGENT_SKILLS_TABLE}"
            ORDER BY rowid ASC;
            """
        )
        for raw_row in cur.fetchall():
            skill_id = str(raw_row[0] or "").strip()
            name = str(raw_row[1] or "").strip()
            description = str(raw_row[2] or "").strip()
            tools, tools_error = _parse_tools_json(raw_row[3])
            instructions = str(raw_row[4] or "").strip()

            if not skill_id:
                errors.append("Skill row ignored: missing id.")
                continue
            if not name:
                errors.append(f"Skill row {skill_id} ignored: name is required.")
                continue
            if tools_error:
                errors.append(f"Skill '{name}' ignored: {tools_error}")
                continue

            rows.append(
                _SQLiteSkillRow(
                    skill_id=skill_id,
                    name=name,
                    description=description,
                    tools=tuple(tools),
                    instructions=instructions,
                )
            )
        return rows, errors
    except sqlite3.Error:
        logger.exception("Failed to read skills from SQLite.")
        errors.append("Failed to read skills table from SQLite.")
        return None, errors
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                logger.debug("Failed closing SQLite connection after skill read", exc_info=True)


def _drop_skill_table() -> None:
    db_path = get_sqlite_db_path()
    if not db_path:
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        conn.execute(f'DROP TABLE IF EXISTS "{AGENT_SKILLS_TABLE}";')
        conn.commit()
    except sqlite3.Error:
        logger.exception("Failed to drop skills table.")
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                logger.debug("Failed closing SQLite connection after skill drop", exc_info=True)


def _rows_match_snapshot(row: _SQLiteSkillRow, baseline: AgentSkillSnapshotRow) -> bool:
    return (
        row.name == baseline.name
        and row.description == baseline.description
        and row.tools == baseline.tools
        and row.instructions == baseline.instructions
    )


def _is_same_skill_content(skill: PersistentAgentSkill, row: _SQLiteSkillRow) -> bool:
    return (
        (skill.description or "").strip() == row.description
        and _normalize_tools_sequence(skill.tools) == row.tools
        and (skill.instructions or "").strip() == row.instructions
    )


def _normalize_tools_sequence(raw_tools) -> tuple[str, ...]:
    if not isinstance(raw_tools, list):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_tools:
        if not isinstance(item, str):
            continue
        tool_id = item.strip()
        if not tool_id or tool_id in seen:
            continue
        seen.add(tool_id)
        normalized.append(tool_id)
    return tuple(normalized)


def _parse_tools_json(raw_value) -> tuple[list[str], Optional[str]]:
    if raw_value is None:
        return [], None

    parsed = raw_value
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return [], None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [], "tools must be a JSON array of canonical tool IDs"

    if not isinstance(parsed, list):
        return [], "tools must be a JSON array"

    normalized: list[str] = []
    seen: set[str] = set()
    for entry in parsed:
        if not isinstance(entry, str):
            return [], "tools entries must be strings"
        tool_id = entry.strip()
        if not tool_id:
            return [], "tools entries cannot be empty"
        if tool_id in seen:
            continue
        seen.add(tool_id)
        normalized.append(tool_id)
    return normalized, None


def _format_timestamp(dt) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except AttributeError:
        return None
