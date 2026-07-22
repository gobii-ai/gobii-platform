import re


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
