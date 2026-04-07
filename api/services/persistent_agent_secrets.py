from typing import Optional

from django.core.exceptions import ValidationError

from api.domain_validation import DomainPatternValidator
from api.models import PersistentAgent, PersistentAgentSecret


def format_validation_error(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        parts = []
        for field, messages in exc.message_dict.items():
            joined = ", ".join(str(message) for message in messages)
            parts.append(f"{field}: {joined}")
        if parts:
            return "; ".join(parts)
    if hasattr(exc, "messages") and exc.messages:
        return "; ".join(str(message) for message in exc.messages)
    return str(exc)


def validate_secret_for_runtime_use(secret) -> str:
    """Validate only the fields needed to inject a secret into a running task.

    Accepts both PersistentAgentSecret and GlobalSecret instances.
    """
    if secret.secret_type != "credential":
        raise ValidationError({"secret_type": "Only credential secrets may be injected into browser tasks."})

    if getattr(secret, "requested", False):
        raise ValidationError({"requested": "Requested secrets do not have a usable value yet."})

    if not secret.domain_pattern:
        raise ValidationError({"domain_pattern": "Domain pattern is required for credential secrets."})

    try:
        DomainPatternValidator.validate_domain_pattern(secret.domain_pattern)
    except ValueError as exc:
        raise ValidationError({"domain_pattern": str(exc)})

    try:
        DomainPatternValidator._validate_secret_key(secret.key)
    except ValueError as exc:
        raise ValidationError({"key": str(exc)})

    value = secret.get_value()
    try:
        DomainPatternValidator._validate_secret_value(value)
    except ValueError as exc:
        raise ValidationError({"value": str(exc)})
    return value


def build_browser_task_secret_payload(
    agent: PersistentAgent,
    secrets: list,
) -> tuple[Optional[bytes], Optional[dict[str, list[str]]], list[dict[str, str]]]:
    """Build the encrypted secret payload for a browser task plus any invalid rows.

    *secrets* may contain both ``PersistentAgentSecret`` and ``GlobalSecret``
    instances — both share the ``domain_pattern``, ``key``, ``get_value()``,
    ``secret_type``, ``id``, and ``created_at`` interface.
    """
    from api.encryption import SecretsEncryption

    secrets_by_domain: dict[str, dict[str, str]] = {}
    secret_keys_by_domain: dict[str, list[str]] = {}
    invalid: list[dict[str, str]] = []

    for secret in secrets:
        try:
            value = validate_secret_for_runtime_use(secret)
        except ValidationError as exc:
            invalid.append(
                {
                    "id": str(secret.id),
                    "key": secret.key,
                    "domain_pattern": secret.domain_pattern,
                    "created_at": secret.created_at.isoformat() if secret.created_at else "",
                    "error": format_validation_error(exc),
                }
            )
            continue
        except Exception as exc:
            invalid.append(
                {
                    "id": str(secret.id),
                    "key": secret.key,
                    "domain_pattern": secret.domain_pattern,
                    "created_at": secret.created_at.isoformat() if secret.created_at else "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        domain = secret.domain_pattern
        if domain not in secrets_by_domain:
            secrets_by_domain[domain] = {}
            secret_keys_by_domain[domain] = []

        secrets_by_domain[domain][secret.key] = value
        secret_keys_by_domain[domain].append(secret.key)

    if not secrets_by_domain:
        return None, None, invalid

    encrypted_secrets = SecretsEncryption.encrypt_secrets(secrets_by_domain, allow_legacy=False)
    return encrypted_secrets, secret_keys_by_domain, invalid
