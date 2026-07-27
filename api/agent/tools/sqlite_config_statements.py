import re
from typing import Any

import sqlparse


AGENT_CONFIG_UPDATE_RE = re.compile(
    r'''\bupdate\s+["`\[]?__agent_config["`\]]?\s+.*?\bset\b'''
    r'''(?P<assignments>(?:(?:'(?:[^']|'')*'|"(?:[^"]|"")*")|'''
    r'''(?!(?:\bwhere\b|\breturning\b))[\s\S])*)''',
    re.IGNORECASE,
)
AGENT_CONFIG_INSERT_RE = re.compile(
    r'\b(?:insert|replace)\s+(?:or\s+\w+\s+)?into\s+["`\[]?__agent_config["`\]]?\s*\((?P<columns>[^)]*)\)',
    re.IGNORECASE | re.DOTALL,
)


def sqlite_statement_assigns_agent_config_field(statement: str, field_name: str) -> bool:
    field = field_name.lower()
    update_match = AGENT_CONFIG_UPDATE_RE.search(statement or "")
    if update_match:
        assignments = update_match.group("assignments")
        return bool(
            re.search(
                rf'(?<![\w"`\]])["`\[]?{re.escape(field)}["`\]]?\s*=',
                assignments,
                re.IGNORECASE,
            )
        )

    insert_match = AGENT_CONFIG_INSERT_RE.search(statement or "")
    if not insert_match:
        return False
    columns = {
        column.strip().strip('"`[]').lower()
        for column in insert_match.group("columns").split(",")
    }
    return field in columns


def sqlite_batch_statements(tool_params: Any) -> list[str]:
    if not isinstance(tool_params, dict):
        return []
    raw_sql = tool_params.get("queries", tool_params.get("sql", tool_params.get("query")))
    raw_items = raw_sql if isinstance(raw_sql, list) else [raw_sql]
    statements: list[str] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, str):
            continue
        statements.extend(statement.strip() for statement in sqlparse.split(raw_item) if statement.strip())
    return statements


def sqlite_params_assign_emotion(tool_params: Any) -> bool:
    """Whether this sqlite_batch call actually wrote the agent's mood.

    Display metadata alone cannot answer this: during the #462 regression window every
    config write was stamped with an `emotion` slot, so a charter edit and a deliberate
    mood clear look identical there. The SQL is the ground truth for what the step did.
    """
    return any(
        sqlite_statement_assigns_agent_config_field(statement, "emotion")
        or sqlite_statement_assigns_agent_config_field(statement, "emotion_timeout_seconds")
        for statement in sqlite_batch_statements(tool_params)
    )
