"""
Agent variable system for placeholder substitution.

Allows tools to set variables (e.g., file URLs) that the LLM can reference
using «var_name» placeholders in messages. Placeholders are substituted with
actual values before sending.

Variable names are file paths (e.g., "/charts/sales_q4.svg"). This ensures
uniqueness—creating multiple files won't cause collisions. The path is
human-readable and matches what the agent sees in tool results.

The «» syntax is intentional—it's visually distinctive and won't conflict
with template languages (Handlebars, Jinja, Mustache all use {{}}). The LLM
never sees actual URLs, forcing it to use variables and preventing corruption
of signed URLs or hallucinated paths.

Usage:
    # In a tool (using path as variable name):
    set_agent_variable("/charts/sales_q4.svg", signed_url)

    # In LLM output:
    "Here's the chart: ![](«/charts/sales_q4.svg»)"

    # Before sending:
    body = substitute_variables(body)
    # Result: "Here's the chart: ![](https://...actual-signed-url...)"
"""
import logging
import re
from contextvars import ContextVar
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Store for agent variables - persists across tool calls within a session
_agent_variables: ContextVar[Dict[str, str]] = ContextVar("agent_variables", default={})

# Pattern for «var_name» placeholders (guillemet quotes - visually distinct, won't conflict with code)
# Matches paths like /charts/sales.svg as well as simple names
_PLACEHOLDER_PATTERN = re.compile(r'«([^»]+)»')


def set_agent_variable(name: str, value: str) -> None:
    """Set a variable that can be referenced in messages as «name».

    Convention: Use file paths as variable names (e.g., "/charts/sales.svg").
    This ensures uniqueness when multiple files are created.
    """
    current = _agent_variables.get({}).copy()
    current[name] = value
    _agent_variables.set(current)
    logger.debug("Set agent variable %s = %s...", name, value[:50] if len(value) > 50 else value)


def get_agent_variable(name: str) -> Optional[str]:
    """Get a variable value by name."""
    return _agent_variables.get({}).get(name)


def get_all_variables() -> Dict[str, str]:
    """Get all current variables (for debugging/context)."""
    return _agent_variables.get({}).copy()


def clear_variables() -> None:
    """Clear all variables (typically at session start)."""
    _agent_variables.set({})


def substitute_variables(text: str) -> str:
    """Replace «var_name» placeholders with actual values.

    If a variable is not found, the placeholder is left unchanged
    (allows LLM to see it wasn't substituted).
    """
    if not text or '«' not in text:
        return text

    variables = _agent_variables.get({})
    if not variables:
        return text

    def replace_match(match: re.Match) -> str:
        var_name = match.group(1)
        if var_name in variables:
            return variables[var_name]
        # Keep original placeholder if variable not found
        logger.debug("Variable «%s» not found, keeping placeholder", var_name)
        return match.group(0)

    return _PLACEHOLDER_PATTERN.sub(replace_match, text)


def format_variables_for_prompt() -> str:
    """Format current variables for inclusion in agent prompt context.

    Shows the agent what variables are available and their placeholders.
    Does NOT show actual values (URLs, paths) to prevent copying.
    """
    variables = _agent_variables.get({})
    if not variables:
        return ""

    lines = ["Available variables (use «name» in messages—substituted automatically when sent):"]
    for name in variables.keys():
        # Don't show value - just the variable name. This prevents LLM from copying URLs.
        lines.append(f"  «{name}»")

    return "\n".join(lines)


def substitute_variables_as_data_uris(text: str, agent) -> str:
    """Replace «path» placeholders with base64 data URIs.

    Used by tools like create_pdf that need embedded content instead of URLs.
    Looks up files in the agent's filespace and converts to data URIs.

    Falls back to regular substitution (signed URLs) if file lookup fails.
    """
    import base64
    from api.models import AgentFsNode
    from api.agent.files.filespace_service import get_or_create_default_filespace

    if not text or '«' not in text:
        return text

    variables = _agent_variables.get({})
    if not variables:
        return text

    # Get agent's filespace for file lookups
    try:
        filespace = get_or_create_default_filespace(agent)
    except Exception:
        logger.warning("Failed to get filespace for agent %s, falling back to URL substitution", agent.id)
        return substitute_variables(text)

    def replace_match(match: re.Match) -> str:
        var_name = match.group(1)

        if var_name not in variables:
            logger.debug("Variable «%s» not found, keeping placeholder", var_name)
            return match.group(0)

        # Variable names are filespace paths - try to load the file
        if var_name.startswith("/"):
            try:
                node = AgentFsNode.objects.filter(
                    filespace=filespace,
                    path=var_name,
                    node_type=AgentFsNode.NodeType.FILE,
                    is_deleted=False,
                ).first()

                if node and node.content:
                    content_bytes = node.content.read()
                    node.content.seek(0)  # Reset for potential re-reads
                    mime_type = node.mime_type or "application/octet-stream"
                    b64 = base64.b64encode(content_bytes).decode("ascii")
                    return f"data:{mime_type};base64,{b64}"
            except Exception:
                logger.warning("Failed to load file %s as data URI, using signed URL", var_name)

        # Fall back to signed URL
        return variables[var_name]

    return _PLACEHOLDER_PATTERN.sub(replace_match, text)
