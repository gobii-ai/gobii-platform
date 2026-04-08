"""Shared helpers for skill metadata."""

import re
from typing import Any, Mapping, Sequence

from api.domain_validation import DomainPatternValidator

SKILL_SECRET_TYPE_CREDENTIAL = "credential"
SKILL_SECRET_TYPE_ENV_VAR = "env_var"
SKILL_SECRET_TYPE_CHOICES = {
    SKILL_SECRET_TYPE_CREDENTIAL,
    SKILL_SECRET_TYPE_ENV_VAR,
}
_ENV_VAR_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def normalize_skill_tool_ids(raw_tools: Any) -> tuple[str, ...]:
    """Return unique, trimmed canonical tool IDs in original order."""
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


def build_skill_tool_ids(
    raw_tools: Any,
    extra_tool_ids: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Return unique required tool ids from stored tools plus extra tool ids."""
    merged: list[str] = list(normalize_skill_tool_ids(raw_tools))
    seen = set(merged)
    for item in extra_tool_ids or ():
        if not isinstance(item, str):
            continue
        tool_id = item.strip()
        if not tool_id or tool_id in seen:
            continue
        seen.add(tool_id)
        merged.append(tool_id)
    return tuple(merged)


def normalize_skill_secret_requirements(raw_secrets: Any) -> tuple[dict[str, str], ...]:
    """Validate and normalize required skill secret definitions."""
    if raw_secrets in (None, ""):
        return ()
    if not isinstance(raw_secrets, list):
        raise ValueError("secrets must be a JSON array")

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(raw_secrets):
        if not isinstance(item, Mapping):
            raise ValueError(f"secrets entry {index + 1} must be an object")

        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError(f"secrets entry {index + 1} is missing name")

        key = str(item.get("key") or "").strip()
        if not key:
            raise ValueError(f"secrets entry {index + 1} is missing key")

        secret_type = str(item.get("secret_type") or "").strip().lower()
        if secret_type not in SKILL_SECRET_TYPE_CHOICES:
            raise ValueError(
                f"secrets entry {index + 1} has invalid secret_type '{secret_type or ''}'"
            )

        description = str(item.get("description") or "").strip()
        domain_pattern = str(item.get("domain_pattern") or "").strip()
        if secret_type == SKILL_SECRET_TYPE_ENV_VAR:
            key = key.upper()
            if not _ENV_VAR_KEY_PATTERN.match(key):
                raise ValueError(
                    f"env_var secret '{name}' must use an env-style key like MY_TOKEN"
                )
            if domain_pattern:
                raise ValueError(
                    f"env_var secret '{name}' cannot set domain_pattern"
                )
            dedupe_key = (secret_type, key, "")
            payload: dict[str, str] = {
                "name": name,
                "key": key,
                "secret_type": secret_type,
                "description": description,
            }
        else:
            if not domain_pattern:
                raise ValueError(
                    f"credential secret '{name}' must include domain_pattern"
                )
            try:
                domain_pattern = DomainPatternValidator.normalize_domain_pattern(domain_pattern)
            except ValueError as exc:
                raise ValueError(
                    f"credential secret '{name}' has invalid domain_pattern: {exc}"
                ) from exc
            dedupe_key = (secret_type, key, domain_pattern)
            payload = {
                "name": name,
                "key": key,
                "secret_type": secret_type,
                "description": description,
                "domain_pattern": domain_pattern,
            }

        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(payload)

    return tuple(normalized)


def format_skill_secret_requirement(secret: Mapping[str, Any]) -> str:
    """Return a concise display label for a normalized secret requirement."""
    name = str(secret.get("name") or "").strip() or "Unnamed secret"
    secret_type = str(secret.get("secret_type") or "").strip().lower()
    key = str(secret.get("key") or "").strip()
    if secret_type == SKILL_SECRET_TYPE_ENV_VAR:
        return f"{name} [{secret_type}:{key}]"
    domain_pattern = str(secret.get("domain_pattern") or "").strip()
    return f"{name} [{secret_type}:{key} @ {domain_pattern}]"
