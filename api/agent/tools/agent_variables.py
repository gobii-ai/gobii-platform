"""
Agent variable system for placeholder substitution.

Allows tools to set variables (e.g., chart URLs, file URLs) that the LLM
can reference using «var_name» placeholders in messages. Placeholders
are substituted with actual values before sending.

The «» syntax is intentional—it's visually distinctive and won't conflict
with template languages (Handlebars, Jinja, Mustache all use {{}}). The LLM
never sees actual URLs, forcing it to use variables and preventing corruption
of signed URLs or hallucinated paths.

Usage:
    # In a tool:
    set_agent_variable("chart_url", signed_url)

    # In LLM output:
    "Here's the chart: ![](«chart_url»)"

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
_PLACEHOLDER_PATTERN = re.compile(r'«(\w+)»')


def set_agent_variable(name: str, value: str) -> None:
    """Set a variable that can be referenced in messages as «name».

    Common variables set by tools:
    - chart_url: URL of the most recently created chart
    - chart_path: Filespace path of the most recently created chart
    - file_url: URL of the most recently saved file
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
