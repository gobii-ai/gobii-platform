"""Helpers for storing and resolving per-agent variables."""

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Set, Tuple

from django.db import transaction

from api.models import PersistentAgent, PersistentAgentToolCall, PersistentAgentVariable

logger = logging.getLogger(__name__)

VAR_REF_RE = re.compile(r"^\$(?P<name>[A-Za-z0-9_\-]+)$")
DEFAULT_MIN_VARIABLE_BYTES = 1024
MAX_VARIABLES_PER_AGENT = 50


class VariableResolutionError(Exception):
    """Raised when a variable reference cannot be resolved."""


def _serialize_value(value: Any) -> tuple[str, bool, int]:
    """Return (text, is_json, size_bytes) for a value."""
    if isinstance(value, str):
        text = value
        return text, False, len(text.encode("utf-8"))

    try:
        text = json.dumps(value)
        return text, True, len(text.encode("utf-8"))
    except Exception:
        text = str(value)
        return text, False, len(text.encode("utf-8"))


def _deserialize_value(var: PersistentAgentVariable) -> Any:
    """Return the concrete value for a variable, parsing JSON when flagged."""
    if not var.is_json:
        return var.value
    try:
        return json.loads(var.value)
    except Exception:
        logger.debug("Failed to decode JSON for variable %s; returning raw text", var.name, exc_info=True)
        return var.value


def _sanitize_name(part: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", part or "")
    cleaned = cleaned.strip("_")
    return cleaned or "var"


def generate_variable_name(tool_call: PersistentAgentToolCall, *, field: str | None = None, prefix: str | None = None) -> str:
    """Create a deterministic variable name for a tool call."""
    base = prefix or tool_call.tool_name or "var"
    field_suffix = field or "result"
    step_part = getattr(tool_call, "step_id", None)
    step_suffix = step_part.hex if hasattr(step_part, "hex") else str(step_part or "") or "step"
    name = "_".join(
        filter(None, [_sanitize_name(base).lower(), step_suffix, _sanitize_name(field_suffix).lower()])
    )
    return name[:128]


def create_variable(
    agent: PersistentAgent,
    *,
    name: str,
    value: Any,
    tool_call: PersistentAgentToolCall | None = None,
    summary: str | None = None,
) -> tuple[PersistentAgentVariable, bool]:
    """Persist a variable if it does not already exist (idempotent)."""
    text, is_json, size_bytes = _serialize_value(value)
    defaults = {
        "value": text,
        "is_json": is_json,
        "size_bytes": size_bytes,
        "tool_call": tool_call,
        "summary": summary or "",
    }
    with transaction.atomic():
        variable, created = PersistentAgentVariable.objects.get_or_create(
            agent=agent,
            name=name,
            defaults=defaults,
        )
        if created:
            qs = PersistentAgentVariable.objects.filter(agent=agent).order_by("-created_at")
            ids_to_keep = list(qs.values_list("id", flat=True)[:MAX_VARIABLES_PER_AGENT])
            if ids_to_keep:
                (
                    PersistentAgentVariable.objects.filter(agent=agent)
                    .exclude(id__in=ids_to_keep)
                    .delete()
                )
        return variable, created


def materialize_variable_value(variable: PersistentAgentVariable) -> Any:
    """Return variable value."""
    return _deserialize_value(variable)


def extract_variableize_config(result: Any) -> tuple[Any, dict | None]:
    """Pop variableization config from a tool result, returning cleaned result and config."""
    if not isinstance(result, dict):
        return result, None
    if "_variableize" not in result:
        return result, None

    result_copy = dict(result)
    config = result_copy.pop("_variableize", None)
    return result_copy, config if isinstance(config, dict) else None


def variableize_from_config(
    agent: PersistentAgent,
    tool_call: PersistentAgentToolCall,
    result_obj: Any,
    config: dict | None,
) -> list[PersistentAgentVariable]:
    """Create variables based on explicit config returned by a tool."""
    if not config:
        return []
    fields: Iterable[str] = config.get("fields") or []
    prefix: str | None = config.get("prefix")

    created: list[PersistentAgentVariable] = []
    if isinstance(result_obj, dict):
        for field in fields:
            if field not in result_obj:
                continue
            name = generate_variable_name(tool_call, field=field, prefix=prefix)
            try:
                var, was_created = create_variable(
                    agent,
                    name=name,
                    value=result_obj[field],
                    tool_call=tool_call,
                    summary=f"{tool_call.tool_name} field '{field}'",
                )
                if was_created:
                    created.append(var)
            except Exception:
                logger.debug("Failed to create variable for field %s on tool %s", field, tool_call.tool_name, exc_info=True)
    return created


def variableize_full_result(
    agent: PersistentAgent,
    tool_call: PersistentAgentToolCall,
    value: Any,
    *,
    min_bytes: int = DEFAULT_MIN_VARIABLE_BYTES,
) -> list[PersistentAgentVariable]:
    """Create a variable from an entire tool result when large enough."""
    # Parse JSON string back if possible
    parsed_value = value
    if isinstance(value, str):
        try:
            parsed_value = json.loads(value)
        except Exception:
            parsed_value = value

    text, is_json, size_bytes = _serialize_value(parsed_value)
    if size_bytes < min_bytes:
        return []

    try:
        var, created = create_variable(
            agent,
            name=generate_variable_name(tool_call),
            value=parsed_value,
            tool_call=tool_call,
            summary=f"{tool_call.tool_name} result",
        )
        return [var] if created else [var]
    except Exception:
        logger.debug("Failed to create variable for tool %s", tool_call.tool_name, exc_info=True)
        return []


def resolve_variables_in_params(agent: PersistentAgent, params: Any) -> tuple[Any, Set[str]]:
    """Resolve `$var` references inside tool params (no dotted paths)."""
    used: Set[str] = set()

    def _resolve(value: Any) -> Any:
        if isinstance(value, str):
            match = VAR_REF_RE.match(value.strip())
            if match:
                var_name = match.group("name")
                variable = PersistentAgentVariable.objects.filter(agent=agent, name=var_name).first()
                if not variable:
                    raise VariableResolutionError(f"Variable ${var_name} not found")
                used.add(var_name)
                return materialize_variable_value(variable)
        if isinstance(value, dict):
            return {k: _resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_resolve(v) for v in value]
        return value

    resolved = _resolve(params)
    return resolved, used


def describe_variables(variables: Iterable[PersistentAgentVariable]) -> str:
    """Return a short catalog string for prompt context."""
    lines: list[str] = []
    for var in variables:
        size_kb = var.size_bytes / 1024 if var.size_bytes else 0
        summary = var.summary or ("JSON" if var.is_json else "Text")
        lines.append(f"${var.name} — {summary} (~{size_kb:.1f} KB)")
    return "\n".join(lines)
