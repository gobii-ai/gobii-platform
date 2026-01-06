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
_MARKDOWN_IMAGE_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<url><[^>]+>|[^)\s]+)(?:\s+['\"][^'\"]*['\"])?\s*\)"
)
_HTML_IMG_SRC_PATTERN = re.compile(
    r"(<img\b[^>]*\bsrc\s*=\s*)(?P<quote>['\"])(?P<url>[^'\"]+)(?P=quote)",
    re.IGNORECASE,
)


def _normalize_filespace_path(raw: str) -> Optional[str]:
    if not raw:
        return None
    value = raw.strip()
    if value.startswith("«") and value.endswith("»"):
        value = value[1:-1].strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "data:", "mailto:", "tel:", "#")):
        return None
    for delimiter in ("?", "#"):
        if delimiter in value:
            value = value.split(delimiter, 1)[0]
    if value.startswith("/"):
        return value
    if "/" in value:
        return f"/{value}"
    return None


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


def substitute_variables_with_filespace(text: str, agent) -> str:
    """Replace «var_name» placeholders with actual values or filespace URLs.

    Falls back to signed filespace URLs when a placeholder looks like a filespace
    path (e.g., «/charts/q4.svg») but no in-memory variable is present.
    """
    if not text:
        return text

    variables = _agent_variables.get({})
    filespace = None
    if agent is not None:
        try:
            from api.agent.files.filespace_service import get_or_create_default_filespace

            filespace = get_or_create_default_filespace(agent)
        except Exception:
            logger.warning(
                "Failed to get filespace for agent %s, variable-only substitution will be used",
                getattr(agent, "id", None),
            )
            filespace = None

    if not variables and not filespace:
        return text

    url_cache: Dict[str, str] = {}

    def _resolve_filespace_url(path: str) -> Optional[str]:
        if not filespace:
            return None
        if path in url_cache:
            return url_cache[path]
        try:
            from api.models import AgentFsNode
            from api.agent.files.attachment_helpers import build_signed_filespace_download_url

            node = AgentFsNode.objects.filter(
                filespace=filespace,
                path=path,
                node_type=AgentFsNode.NodeType.FILE,
                is_deleted=False,
            ).only("id").first()
            if not node:
                return None
            url = build_signed_filespace_download_url(
                agent_id=str(agent.id),
                node_id=node.id,
            )
            url_cache[path] = url
            return url
        except Exception:
            logger.warning("Failed to resolve filespace URL for %s", path)
            return None

    def _resolve_value(raw: str) -> Optional[str]:
        value = (raw or "").strip()
        if not value:
            return None
        if value in variables:
            return variables[value]
        normalized = _normalize_filespace_path(value)
        if not normalized:
            return None
        if normalized in variables:
            return variables[normalized]
        return _resolve_filespace_url(normalized)

    def replace_match(match: re.Match) -> str:
        var_name = match.group(1)
        resolved = _resolve_value(var_name)
        if resolved:
            return resolved
        logger.debug("Variable «%s» not found, keeping placeholder", var_name)
        return match.group(0)

    substituted = _PLACEHOLDER_PATTERN.sub(replace_match, text)

    def replace_markdown_image(match: re.Match) -> str:
        raw_url = match.group("url")
        resolved = _resolve_value(raw_url)
        if not resolved:
            return match.group(0)
        alt = match.group("alt")
        return f"![{alt}]({resolved})"

    def replace_html_image(match: re.Match) -> str:
        raw_url = match.group("url")
        resolved = _resolve_value(raw_url)
        if not resolved:
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group(1)}{quote}{resolved}{quote}"

    substituted = _MARKDOWN_IMAGE_PATTERN.sub(replace_markdown_image, substituted)
    substituted = _HTML_IMG_SRC_PATTERN.sub(replace_html_image, substituted)
    return substituted


def format_variables_for_prompt() -> str:
    """Format current variables for inclusion in agent prompt context.

    Shows the agent what variables are available and their placeholders.
    Does NOT show actual values (URLs, paths) to prevent copying.
    """
    variables = _agent_variables.get({})
    if not variables:
        return ""

    lines = [
        "Available file variables (use «name» in messages; for attachments, pass the same «name»):"
    ]
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

    if not text:
        return text

    variables = _agent_variables.get({})

    # Get agent's filespace for file lookups
    filespace = None
    try:
        filespace = get_or_create_default_filespace(agent)
    except Exception:
        logger.warning("Failed to get filespace for agent %s, falling back to URL substitution", agent.id)
        filespace = None

    if not variables and not filespace:
        return text

    data_uri_cache: Dict[str, str] = {}

    def _load_data_uri(path: str) -> Optional[str]:
        if not filespace:
            return None
        if path in data_uri_cache:
            return data_uri_cache[path]
        try:
            node = AgentFsNode.objects.filter(
                filespace=filespace,
                path=path,
                node_type=AgentFsNode.NodeType.FILE,
                is_deleted=False,
            ).first()

            if node and node.content:
                content_bytes = node.content.read()
                node.content.seek(0)  # Reset for potential re-reads
                mime_type = node.mime_type or "application/octet-stream"
                b64 = base64.b64encode(content_bytes).decode("ascii")
                data_uri = f"data:{mime_type};base64,{b64}"
                data_uri_cache[path] = data_uri
                return data_uri
        except Exception:
            logger.warning("Failed to load file %s as data URI", path)
        return None

    def _resolve_value(raw: str) -> Optional[str]:
        value = (raw or "").strip()
        if not value:
            return None
        if value in variables:
            return variables[value]
        normalized = _normalize_filespace_path(value)
        if not normalized:
            return None
        data_uri = _load_data_uri(normalized)
        if data_uri:
            return data_uri
        if normalized in variables:
            return variables[normalized]
        return None

    def replace_match(match: re.Match) -> str:
        var_name = match.group(1)
        resolved = _resolve_value(var_name)
        if resolved:
            return resolved
        logger.debug("Variable «%s» not found, keeping placeholder", var_name)
        return match.group(0)

    substituted = _PLACEHOLDER_PATTERN.sub(replace_match, text)

    def replace_html_image(match: re.Match) -> str:
        raw_url = match.group("url")
        resolved = _resolve_value(raw_url)
        if not resolved:
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group(1)}{quote}{resolved}{quote}"

    substituted = _HTML_IMG_SRC_PATTERN.sub(replace_html_image, substituted)
    return substituted
