"""
SQLite-backed agent config helpers.

Seeds an ephemeral config table for each LLM invocation and applies updates
after tool execution. This keeps charter/schedule changes in SQLite while
persisting final values to Postgres.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from .charter_updater import execute_update_charter
from .schedule_updater import execute_update_schedule
from .sqlite_guardrails import clear_guarded_connection, open_guarded_sqlite_connection
from .sqlite_state import AGENT_CONFIG_TABLE, get_sqlite_db_path

logger = logging.getLogger(__name__)


NAMED_CHANNEL_TOOL_PATTERNS = {
    "slack": re.compile(r"\bslack\b", re.IGNORECASE),
    "email": re.compile(r"\bemail\b", re.IGNORECASE),
    "sms": re.compile(r"\bsms\b|\btext messages?\b|\btexts?\b", re.IGNORECASE),
    "google sheets": re.compile(r"\bgoogle sheets\b|\bsheets\b", re.IGNORECASE),
    "hubspot": re.compile(r"\bhubspot\b", re.IGNORECASE),
    "salesforce": re.compile(r"\bsalesforce\b", re.IGNORECASE),
    "discord": re.compile(r"\bdiscord\b", re.IGNORECASE),
    "notion": re.compile(r"\bnotion\b", re.IGNORECASE),
}
DELIVERY_OR_TOOL_CLAUSE_RE = re.compile(
    r"\b(send|deliver|message|notify|post|publish|share|sync|write|update|append|brief|alert|email|text)\b",
    re.IGNORECASE,
)
GENERIC_CHANNEL_RE = re.compile(
    r"\b(web chat|chat|current channel|available channel|best channel|default channel|usual channel)\b",
    re.IGNORECASE,
)
CHANNEL_CHANGE_RE = re.compile(
    r"\b(no longer|stop|don't|do not|remove|drop|instead|replace|switch|change)\b",
    re.IGNORECASE,
)
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")


@dataclass(frozen=True)
class AgentConfigSnapshot:
    charter: str
    schedule: Optional[str]


@dataclass(frozen=True)
class AgentConfigApplyResult:
    updated_fields: Sequence[str]
    errors: Sequence[str]


def seed_sqlite_agent_config(agent) -> Optional[AgentConfigSnapshot]:
    """Create/reset the agent config table and seed it with current values."""
    db_path = get_sqlite_db_path()
    if not db_path:
        logger.warning("SQLite DB path unavailable; cannot seed agent config.")
        return None

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        conn.execute(f'DROP TABLE IF EXISTS "{AGENT_CONFIG_TABLE}";')
        conn.execute(
            f"""
            CREATE TABLE "{AGENT_CONFIG_TABLE}" (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                charter TEXT,
                schedule TEXT
            );
            """
        )
        charter = agent.charter or ""
        schedule = agent.schedule
        conn.execute(
            f'INSERT INTO "{AGENT_CONFIG_TABLE}" (id, charter, schedule) VALUES (1, ?, ?);',
            (charter, schedule),
        )
        conn.commit()
        return AgentConfigSnapshot(charter=charter, schedule=schedule)
    except Exception:
        logger.exception("Failed to seed agent config table for agent %s", getattr(agent, "id", None))
        return None
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def apply_sqlite_agent_config_updates(
    agent,
    baseline: Optional[AgentConfigSnapshot],
) -> AgentConfigApplyResult:
    """Apply any SQLite config updates to the persistent agent record."""
    updated_fields: list[str] = []
    errors: list[str] = []
    current = _read_agent_config_snapshot()

    if baseline is None or current is None:
        _drop_agent_config_table()
        return AgentConfigApplyResult(updated_fields=updated_fields, errors=errors)

    if _normalize_charter(current.charter) != _normalize_charter(baseline.charter):
        new_charter = _preserve_named_channel_tool_guidance(
            agent,
            baseline.charter,
            _normalize_charter(current.charter),
        )
        result = execute_update_charter(agent, {"new_charter": new_charter})
        if isinstance(result, dict) and result.get("status") == "ok":
            updated_fields.append("charter")
        else:
            errors.append(result.get("message", "Charter update failed.") if isinstance(result, dict) else "Charter update failed.")

    if _normalize_schedule(current.schedule) != _normalize_schedule(baseline.schedule):
        result = execute_update_schedule(agent, {"new_schedule": current.schedule})
        if isinstance(result, dict) and result.get("status") == "ok":
            updated_fields.append("schedule")
        else:
            errors.append(result.get("message", "Schedule update failed.") if isinstance(result, dict) else "Schedule update failed.")

    _drop_agent_config_table()
    return AgentConfigApplyResult(updated_fields=updated_fields, errors=errors)


def _read_agent_config_snapshot() -> Optional[AgentConfigSnapshot]:
    db_path = get_sqlite_db_path()
    if not db_path:
        return None

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        cur = conn.cursor()
        cur.execute(f'SELECT charter, schedule FROM "{AGENT_CONFIG_TABLE}" WHERE id = 1;')
        row = cur.fetchone()
        if not row:
            return None
        return AgentConfigSnapshot(charter=row[0] or "", schedule=row[1])
    except Exception:
        logger.exception("Failed to read agent config table.")
        return None
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def _drop_agent_config_table() -> None:
    db_path = get_sqlite_db_path()
    if not db_path:
        return

    conn = None
    try:
        conn = open_guarded_sqlite_connection(db_path)
        conn.execute(f'DROP TABLE IF EXISTS "{AGENT_CONFIG_TABLE}";')
        conn.commit()
    except Exception:
        logger.exception("Failed to drop agent config table.")
    finally:
        if conn is not None:
            try:
                clear_guarded_connection(conn)
                conn.close()
            except Exception:
                pass


def _normalize_charter(value: Optional[str]) -> str:
    return (value or "").strip()


def _preserve_named_channel_tool_guidance(agent, baseline_charter: str, new_charter: str) -> str:
    baseline_sentences = _split_sentences(baseline_charter)
    if not baseline_sentences or not new_charter.strip():
        return new_charter

    new_terms = _named_channel_tool_terms(new_charter)
    missing_sentences: list[str] = []
    missing_terms: set[str] = set()
    for sentence in baseline_sentences:
        sentence_terms = _named_channel_tool_terms(sentence)
        lost_terms = sentence_terms - new_terms
        if not lost_terms:
            continue
        missing_sentences.append(sentence)
        missing_terms.update(lost_terms)

    if not missing_sentences or _latest_user_text_requested_channel_change(agent, missing_terms):
        return new_charter

    preserved = []
    for sentence in _split_sentences(new_charter):
        if (
            DELIVERY_OR_TOOL_CLAUSE_RE.search(sentence)
            and GENERIC_CHANNEL_RE.search(sentence)
            and not _named_channel_tool_terms(sentence)
        ):
            continue
        preserved.append(sentence)

    preserved_lower = {sentence.lower() for sentence in preserved}
    for sentence in missing_sentences:
        if sentence.lower() not in preserved_lower:
            preserved.append(sentence)

    return " ".join(preserved)


def _split_sentences(value: str) -> list[str]:
    return [match.group(0).strip() for match in SENTENCE_RE.finditer(value or "") if match.group(0).strip()]


def _named_channel_tool_terms(value: str) -> set[str]:
    return {
        term
        for term, pattern in NAMED_CHANNEL_TOOL_PATTERNS.items()
        if pattern.search(value or "")
    }


def _latest_user_text_requested_channel_change(agent, lost_terms: set[str]) -> bool:
    if not lost_terms:
        return False

    from api.models import PersistentAgentMessage

    latest_text = (
        PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=False)
        .order_by("-timestamp", "-seq")
        .values_list("body", flat=True)
        .first()
    )
    if not latest_text or not CHANNEL_CHANGE_RE.search(latest_text):
        return False

    mentioned_terms = _named_channel_tool_terms(latest_text)
    return bool(mentioned_terms & lost_terms or mentioned_terms - lost_terms)


def _normalize_schedule(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
