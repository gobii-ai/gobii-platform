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
from django.db.models import F, Q
from django.utils import timezone

from api.models import (
    GlobalSecret,
    PersistentAgentKanbanCard,
    PersistentAgentSecret,
    PersistentAgentSkill,
    PersistentAgentSystemSkillState,
)
from api.agent.system_skills.registry import get_system_skill_definition
from api.agent.system_skills.service import (
    ensure_default_system_skills_enabled,
    get_enabled_system_skill_states,
    refresh_system_skills_for_tool,
)
from api.services.skill_analytics import (
    SKILL_ORIGIN_FORKED_FROM_GLOBAL,
    infer_agent_skill_origin,
    track_agent_skill_event,
)
from util.analytics import AnalyticsEvent

from .skill_utils import (
    format_skill_secret_requirement,
    normalize_skill_secret_requirements,
    normalize_skill_tool_ids,
)
from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import AGENT_SKILLS_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentSkillSnapshotRow:
    skill_id: str
    name: str
    description: str
    tools: tuple[str, ...]
    secrets: tuple[dict[str, str], ...]
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
    secrets: tuple[dict[str, str], ...]
    instructions: str


@dataclass(frozen=True)
class _PromptSkillEntry:
    rendered: str
    last_used_at: object
    fallback_at: object
    label: str
    sort_name: str
    omitted_name: str


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
                secrets TEXT NOT NULL DEFAULT '[]',
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
            tools = normalize_skill_tool_ids(skill.tools)
            secrets = normalize_skill_secret_requirements(skill.secrets)
            instructions = (skill.instructions or "").strip()
            rows.append(
                (
                    skill_id,
                    name,
                    description,
                    version,
                    json.dumps(list(tools)),
                    json.dumps(list(secrets)),
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
                secrets=secrets,
                instructions=instructions,
            )

        if rows:
            conn.executemany(
                f"""
                INSERT INTO "{AGENT_SKILLS_TABLE}"
                    (id, name, description, version, tools, secrets, instructions, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
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
    pending_events: list[dict[str, object]] = []

    current_rows, read_errors, invalid_skill_ids, invalid_skill_names = _read_sqlite_skills()
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
    protected_names = set(invalid_skill_names)
    for skill_id in invalid_skill_ids:
        baseline_row = baseline.by_id.get(skill_id)
        if baseline_row:
            protected_names.add(baseline_row.name)
    deleted_names = sorted(
        name for name in baseline.names if name not in current_names and name not in protected_names
    )

    candidates_by_name: dict[str, _SQLiteSkillRow] = {}
    for row in current_rows:
        baseline_row = baseline.by_id.get(row.skill_id)
        if baseline_row and _rows_match_snapshot(row, baseline_row):
            continue
        candidates_by_name[row.name] = row

    existing_rows = list(
        PersistentAgentSkill.objects.filter(agent=agent)
        .select_related("global_skill")
        .order_by("name", "-version", "-updated_at")
    )
    latest_by_name: dict[str, PersistentAgentSkill] = {}
    global_source_by_name: dict[str, object] = {}
    for existing in existing_rows:
        if existing.name not in latest_by_name:
            latest_by_name[existing.name] = existing
        if existing.name not in global_source_by_name and existing.global_skill is not None:
            global_source_by_name[existing.name] = existing.global_skill

    with transaction.atomic():
        if deleted_names:
            deleted_rows = {
                name: latest_by_name[name]
                for name in deleted_names
                if name in latest_by_name
            }
            PersistentAgentSkill.objects.filter(
                agent=agent,
                name__in=deleted_names,
            ).delete()
            for name, deleted_row in deleted_rows.items():
                had_global_ancestor = name in global_source_by_name
                pending_events.append(
                    {
                        "event": AnalyticsEvent.PERSISTENT_AGENT_SKILL_DELETED,
                        "skill_name": name,
                        "skill_version": deleted_row.version,
                        "tools": deleted_row.tools,
                        "skill_origin": infer_agent_skill_origin(
                            deleted_row,
                            had_global_ancestor=had_global_ancestor,
                        ),
                        "global_skill": global_source_by_name.get(name),
                    }
                )

        valid_tool_ids: set[str] = set()
        if candidates_by_name:
            from .tool_manager import get_available_tool_ids

            valid_tool_ids = get_available_tool_ids(agent)

        for name, row in candidates_by_name.items():
            unknown = [tool_id for tool_id in row.tools if tool_id not in valid_tool_ids]
            if unknown:
                errors.append(
                    f"Skill '{name}' rejected: unknown canonical tool id(s): {', '.join(unknown)}"
                )
                continue

            latest = latest_by_name.get(name)
            if latest and _is_same_skill_content(latest, row):
                continue

            next_version = (latest.version if latest else 0) + 1
            created_skill = PersistentAgentSkill.objects.create(
                agent=agent,
                global_skill=None,
                name=name,
                description=row.description,
                version=next_version,
                tools=list(row.tools),
                secrets=list(row.secrets),
                instructions=row.instructions,
            )
            created_versions.append(f"{name}@{next_version}")
            had_global_ancestor = name in global_source_by_name
            if latest is None:
                event = AnalyticsEvent.PERSISTENT_AGENT_SKILL_CREATED
                skill_origin = infer_agent_skill_origin(None, had_global_ancestor=False)
            elif latest.global_skill_id:
                event = AnalyticsEvent.PERSISTENT_AGENT_GLOBAL_SKILL_FORKED
                skill_origin = SKILL_ORIGIN_FORKED_FROM_GLOBAL
            else:
                event = AnalyticsEvent.PERSISTENT_AGENT_SKILL_UPDATED
                skill_origin = infer_agent_skill_origin(
                    latest,
                    had_global_ancestor=had_global_ancestor,
                )
            pending_events.append(
                {
                    "event": event,
                    "skill_name": name,
                    "skill_version": created_skill.version,
                    "tools": created_skill.tools,
                    "skill_origin": skill_origin,
                    "global_skill": global_source_by_name.get(name),
                }
            )
            latest_by_name[name] = created_skill

    _drop_skill_table()
    for pending_event in pending_events:
        track_agent_skill_event(
            agent=agent,
            event=pending_event["event"],
            skill_name=pending_event["skill_name"],
            skill_version=pending_event.get("skill_version"),
            tools=pending_event.get("tools"),
            skill_origin=pending_event["skill_origin"],
            global_skill=pending_event.get("global_skill"),
        )
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
        key=lambda row: (
            row.last_used_at is not None,
            row.last_used_at or row.updated_at,
            row.updated_at,
        ),
        reverse=True,
    )


def get_required_skill_tool_ids(agent) -> set[str]:
    """Return the union of canonical tool IDs required by latest skill versions."""
    required: set[str] = set()
    for skill in get_latest_skill_versions(agent):
        for tool_id in normalize_skill_tool_ids(skill.tools):
            required.add(tool_id)
    return required


def _get_global_secrets_for_agent(agent):
    if agent.organization_id:
        owner_filter = Q(organization=agent.organization)
    else:
        owner_filter = Q(user=agent.user, organization__isnull=True)
    return GlobalSecret.objects.filter(owner_filter)


def _get_skill_secret_status_sets(agent) -> dict[str, set[tuple[str, str]] | set[str]]:
    available_env_keys = set(
        PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=False,
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
        ).values_list("key", flat=True)
    )
    pending_env_keys = set(
        PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=True,
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
        ).values_list("key", flat=True)
    )
    available_credentials = set(
        PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=False,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
        ).values_list("key", "domain_pattern")
    )
    pending_credentials = set(
        PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=True,
            secret_type=PersistentAgentSecret.SecretType.CREDENTIAL,
        ).values_list("key", "domain_pattern")
    )

    global_qs = _get_global_secrets_for_agent(agent)
    available_env_keys.update(
        global_qs.filter(secret_type=GlobalSecret.SecretType.ENV_VAR).values_list("key", flat=True)
    )
    available_credentials.update(
        global_qs.filter(secret_type=GlobalSecret.SecretType.CREDENTIAL).values_list("key", "domain_pattern")
    )

    return {
        "available_env_keys": available_env_keys,
        "pending_env_keys": pending_env_keys,
        "available_credentials": available_credentials,
        "pending_credentials": pending_credentials,
    }


def _classify_skill_secret_requirements(
    normalized_secrets: tuple[dict[str, str], ...],
    status_sets: dict[str, set[tuple[str, str]] | set[str]],
) -> tuple[list[str], list[str], list[str]]:
    required_labels: list[str] = []
    pending_labels: list[str] = []
    missing_labels: list[str] = []

    available_env_keys = status_sets["available_env_keys"]
    pending_env_keys = status_sets["pending_env_keys"]
    available_credentials = status_sets["available_credentials"]
    pending_credentials = status_sets["pending_credentials"]

    for secret in normalized_secrets:
        label = format_skill_secret_requirement(secret)
        required_labels.append(label)
        if secret["secret_type"] == PersistentAgentSecret.SecretType.ENV_VAR:
            key = secret["key"]
            if key in available_env_keys:
                continue
            if key in pending_env_keys:
                pending_labels.append(label)
                continue
            missing_labels.append(label)
            continue

        credential_key = (secret["key"], secret["domain_pattern"])
        if credential_key in available_credentials:
            continue
        if credential_key in pending_credentials:
            pending_labels.append(label)
            continue
        missing_labels.append(label)

    return required_labels, pending_labels, missing_labels


def _render_saved_skill_for_prompt(skill: PersistentAgentSkill, secret_status_sets) -> str:
    tools = normalize_skill_tool_ids(skill.tools)
    tool_text = ", ".join(tools) if tools else "(none)"
    description = (skill.description or "").strip() or "(no description)"
    try:
        normalized_secrets = normalize_skill_secret_requirements(skill.secrets)
        required_secrets, pending_secrets, missing_secrets = _classify_skill_secret_requirements(
            normalized_secrets,
            secret_status_sets,
        )
        required_secret_text = ", ".join(required_secrets) if required_secrets else "(none)"
    except ValueError as exc:
        required_secret_text = f"(invalid: {exc})"
        pending_secrets = []
        missing_secrets = []
    instructions = (skill.instructions or "").strip()
    if not instructions:
        instructions = "(no instructions)"
    lines = [
        f"Skill: {skill.name} (v{skill.version})",
        f"Description: {description}",
        f"Tools: {tool_text}",
        f"Required secrets: {required_secret_text}",
    ]
    if pending_secrets:
        lines.append(f"Pending secrets: {', '.join(pending_secrets)}")
        lines.append(
            "Pending secrets were already requested. Follow up with the user instead of requesting them again."
        )
    if missing_secrets:
        lines.append(f"Missing secrets: {', '.join(missing_secrets)}")
        lines.append(
            "If you need these to use the skill, request them with `secure_credentials_request` using the listed type/key details."
        )
    lines.extend(
        [
            "Instructions:",
            instructions,
        ]
    )
    return "\n".join(lines)


def _format_current_plan_state(agent) -> str:
    cards = list(
        PersistentAgentKanbanCard.objects.filter(assigned_agent=agent)
        .only("title", "status", "priority", "created_at")
        .order_by("-priority", "created_at")
    )
    if not cards:
        return "Current plan: none"

    groups = {
        PersistentAgentKanbanCard.Status.DOING: [],
        PersistentAgentKanbanCard.Status.TODO: [],
        PersistentAgentKanbanCard.Status.DONE: [],
    }
    for card in cards:
        if card.status in groups:
            groups[card.status].append(card.title)

    lines = [
        "Current plan:",
        f"- Doing: {len(groups[PersistentAgentKanbanCard.Status.DOING])}",
        f"- Todo: {len(groups[PersistentAgentKanbanCard.Status.TODO])}",
        f"- Done: {len(groups[PersistentAgentKanbanCard.Status.DONE])}",
    ]
    for label, status in (
        ("Doing", PersistentAgentKanbanCard.Status.DOING),
        ("Todo", PersistentAgentKanbanCard.Status.TODO),
        ("Done", PersistentAgentKanbanCard.Status.DONE),
    ):
        titles = groups[status]
        if titles:
            lines.append(f"{label}:")
            lines.extend(f"- {title}" for title in titles[:20])
    return "\n".join(lines)


def _render_system_skill_for_prompt(agent, state: PersistentAgentSystemSkillState) -> str | None:
    definition = get_system_skill_definition(state.skill_key)
    if definition is None or not definition.prompt_instructions:
        return None

    tool_text = ", ".join(definition.tool_names) if definition.tool_names else "(none)"
    lines = [
        f"System Skill: {definition.name}",
        f"Key: {definition.skill_key}",
        f"Tools: {tool_text}",
        "Instructions:",
        definition.prompt_instructions.strip(),
    ]
    if definition.skill_key == "runtime_planning":
        lines.extend(["", _format_current_plan_state(agent)])
    return "\n".join(lines)


def _prompt_skill_sort_key(entry: _PromptSkillEntry):
    return (
        entry.last_used_at is not None,
        entry.last_used_at or entry.fallback_at,
        entry.fallback_at,
        entry.label,
    )


def format_recent_skills_for_prompt(agent, limit: int = 3) -> str:
    """Format the top skills by use recency for a high-priority prompt section."""
    ensure_default_system_skills_enabled(agent)
    if limit <= 0:
        return ""

    secret_status_sets = _get_skill_secret_status_sets(agent)
    entries: list[_PromptSkillEntry] = []

    for skill in get_latest_skill_versions(agent):
        entries.append(
            _PromptSkillEntry(
                rendered=_render_saved_skill_for_prompt(skill, secret_status_sets),
                last_used_at=skill.last_used_at,
                fallback_at=skill.updated_at,
                label=f"skill:{skill.name}",
                sort_name=skill.name,
                omitted_name=skill.name,
            )
        )

    for state in get_enabled_system_skill_states(agent):
        definition = get_system_skill_definition(state.skill_key)
        if definition is None:
            continue
        rendered = _render_system_skill_for_prompt(agent, state)
        if not rendered:
            continue
        entries.append(
            _PromptSkillEntry(
                rendered=rendered,
                last_used_at=state.last_used_at,
                fallback_at=state.enabled_at,
                label=f"system:{state.skill_key}",
                sort_name=definition.name,
                omitted_name=f"System Skill: {definition.name} ({definition.skill_key})",
            )
        )

    entries.sort(key=_prompt_skill_sort_key, reverse=True)
    included_entries = sorted(
        entries[:limit],
        key=lambda entry: (entry.sort_name.casefold(), entry.label),
    )
    omitted_entries = sorted(
        entries[limit:],
        key=lambda entry: (entry.sort_name.casefold(), entry.label),
    )
    rendered_blocks = [entry.rendered for entry in included_entries]
    omitted = [entry.omitted_name for entry in omitted_entries]
    if omitted:
        omitted_lines = [
            "Omitted skills due to prompt limit:",
            *[f"- {name}" for name in omitted],
            "Use `search_tools` with an exact omitted skill name or key if you need that skill again.",
        ]
        rendered_blocks.append("\n".join(omitted_lines))
    return "\n\n".join(rendered_blocks)


def refresh_skills_for_tool(agent, tool_name: str) -> list[str]:
    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        return []

    used_at = timezone.now()
    refreshed: list[str] = []
    latest = get_latest_skill_versions(agent)
    for skill in latest:
        if normalized_tool not in normalize_skill_tool_ids(skill.tools):
            continue
        PersistentAgentSkill.objects.filter(id=skill.id).update(
            last_used_at=used_at,
            usage_count=F("usage_count") + 1,
        )
        refreshed.append(skill.name)

    refreshed.extend(refresh_system_skills_for_tool(agent, normalized_tool, used_at=used_at))
    return refreshed


def _read_sqlite_skills() -> tuple[Optional[list[_SQLiteSkillRow]], list[str], set[str], set[str]]:
    db_path = get_sqlite_db_path()
    if not db_path:
        return None, ["SQLite DB path unavailable; cannot read skills table."], set(), set()

    conn = None
    errors: list[str] = []
    rows: list[_SQLiteSkillRow] = []
    invalid_skill_ids: set[str] = set()
    invalid_skill_names: set[str] = set()

    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, name, description, tools, secrets, instructions
            FROM "{AGENT_SKILLS_TABLE}"
            ORDER BY rowid ASC;
            """
        )
        for raw_row in cur.fetchall():
            skill_id = str(raw_row[0] or "").strip()
            name = str(raw_row[1] or "").strip()
            description = str(raw_row[2] or "").strip()
            tools, tools_error = _parse_tools_json(raw_row[3])
            secrets, secrets_error = _parse_secrets_json(raw_row[4])
            instructions = str(raw_row[5] or "").strip()

            if not skill_id:
                errors.append("Skill row ignored: missing id.")
                continue
            if not name:
                errors.append(f"Skill row {skill_id} ignored: name is required.")
                invalid_skill_ids.add(skill_id)
                continue
            if tools_error:
                errors.append(f"Skill '{name}' ignored: {tools_error}")
                invalid_skill_ids.add(skill_id)
                invalid_skill_names.add(name)
                continue
            if secrets_error:
                errors.append(f"Skill '{name}' ignored: {secrets_error}")
                invalid_skill_ids.add(skill_id)
                invalid_skill_names.add(name)
                continue

            rows.append(
                _SQLiteSkillRow(
                    skill_id=skill_id,
                    name=name,
                    description=description,
                    tools=tuple(tools),
                    secrets=tuple(secrets),
                    instructions=instructions,
                )
            )
        return rows, errors, invalid_skill_ids, invalid_skill_names
    except sqlite3.Error:
        logger.exception("Failed to read skills from SQLite.")
        errors.append("Failed to read skills table from SQLite.")
        return None, errors, invalid_skill_ids, invalid_skill_names
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
        and row.secrets == baseline.secrets
        and row.instructions == baseline.instructions
    )


def _is_same_skill_content(skill: PersistentAgentSkill, row: _SQLiteSkillRow) -> bool:
    return (
        (skill.description or "").strip() == row.description
        and normalize_skill_tool_ids(skill.tools) == row.tools
        and normalize_skill_secret_requirements(skill.secrets) == row.secrets
        and (skill.instructions or "").strip() == row.instructions
    )


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


def _parse_secrets_json(raw_value) -> tuple[list[dict[str, str]], Optional[str]]:
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
            return [], "secrets must be a JSON array of secret requirement objects"

    try:
        normalized = normalize_skill_secret_requirements(parsed)
    except ValueError as exc:
        return [], str(exc)
    return list(normalized), None


def _format_timestamp(dt) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except AttributeError:
        return None
