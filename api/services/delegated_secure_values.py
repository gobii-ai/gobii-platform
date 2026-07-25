import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from api.domain_validation import DomainPatternValidator
from api.models import DelegatedSecureValue, PersistentAgent


SECURE_VALUE_REF_PREFIX = "sv_"
DEFAULT_SECURE_VALUE_TTL_SECONDS = 3600
MAX_SECURE_VALUE_TTL_SECONDS = 86400
_SECURE_VALUE_REF_RE = re.compile(r"^sv_([0-9a-fA-F-]{36})$")


class SecureValueError(ValueError):
    pass


@dataclass(frozen=True)
class SecureValueConsumption:
    applied: bool
    already_applied: bool


def create_delegated_secure_value(
    source_agent: PersistentAgent,
    *,
    label: str,
    value: str,
    ttl_seconds: int = DEFAULT_SECURE_VALUE_TTL_SECONDS,
) -> str:
    normalized_label = str(label or "").strip()
    if not normalized_label or len(normalized_label) > 128:
        raise SecureValueError("Secure value labels must be between 1 and 128 characters.")

    normalized_value = str(value)
    try:
        DomainPatternValidator._validate_secret_value(normalized_value)
    except ValueError as exc:
        raise SecureValueError(str(exc)) from exc

    try:
        normalized_ttl = int(ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise SecureValueError("ttl_seconds must be an integer.") from exc
    if normalized_ttl < 60 or normalized_ttl > MAX_SECURE_VALUE_TTL_SECONDS:
        raise SecureValueError(
            f"ttl_seconds must be between 60 and {MAX_SECURE_VALUE_TTL_SECONDS}."
        )

    secure_value = DelegatedSecureValue(
        source_agent=source_agent,
        label=normalized_label,
        expires_at=timezone.now() + timedelta(seconds=normalized_ttl),
    )
    secure_value.set_value(normalized_value)
    secure_value.save()
    return f"{SECURE_VALUE_REF_PREFIX}{secure_value.id}"


def consume_delegated_secure_value(
    source_agent: PersistentAgent,
    target_agent: PersistentAgent,
    *,
    secure_value_ref: str,
    destination: str,
    apply: Callable[[str], None],
) -> SecureValueConsumption:
    secure_value_id = _parse_secure_value_ref(secure_value_ref)
    normalized_destination = str(destination or "").strip()
    if not normalized_destination or len(normalized_destination) > 256:
        raise SecureValueError("A valid secure value destination is required.")

    with transaction.atomic():
        secure_value = (
            DelegatedSecureValue.objects.select_for_update()
            .filter(id=secure_value_id, source_agent=source_agent)
            .first()
        )
        if secure_value is None:
            raise SecureValueError("Secure value reference was not found or is not accessible.")

        if secure_value.consumed_at:
            if (
                secure_value.consumed_by_agent_id == target_agent.id
                and secure_value.consumption_destination == normalized_destination
            ):
                return SecureValueConsumption(applied=False, already_applied=True)
            raise SecureValueError("Secure value reference has already been consumed.")

        if secure_value.expires_at <= timezone.now():
            raise SecureValueError("Secure value reference has expired.")

        apply(secure_value.get_value())
        secure_value.consumed_at = timezone.now()
        secure_value.consumed_by_agent = target_agent
        secure_value.consumption_destination = normalized_destination
        secure_value.encrypted_value = b""
        secure_value.save(
            update_fields=[
                "consumed_at",
                "consumed_by_agent",
                "consumption_destination",
                "encrypted_value",
            ]
        )

    return SecureValueConsumption(applied=True, already_applied=False)


def _parse_secure_value_ref(raw_ref: str) -> uuid.UUID:
    match = _SECURE_VALUE_REF_RE.fullmatch(str(raw_ref or "").strip())
    if not match:
        raise SecureValueError("Invalid secure value reference.")
    try:
        return uuid.UUID(match.group(1))
    except ValueError as exc:
        raise SecureValueError("Invalid secure value reference.") from exc
