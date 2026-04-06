from typing import Optional

from django.core.exceptions import ValidationError

from api.domain_validation import DomainPatternValidator
from api.models import PersistentAgent, PersistentAgentSecret
from django.db.models import Q

def get_secrets_q_for_agent(agent: PersistentAgent) -> Q:
    """Return a Q object matching all secrets visible to this agent (agent-owned + org/user-owned global secrets)."""
    agent_condition = Q(agent=agent)
    if agent.organization_id:
        owner_condition = Q(organization_id=agent.organization_id)
    else:
        owner_condition = Q(user_id=agent.user_id, organization__isnull=True)
    return agent_condition | owner_condition

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


def validate_secret_for_runtime_use(secret: PersistentAgentSecret) -> str:
    """Validate only the fields needed to inject a secret into a running task."""
    if secret.secret_type != PersistentAgentSecret.SecretType.CREDENTIAL:
        raise ValidationError({"secret_type": "Only credential secrets may be injected into browser tasks."})

    if secret.requested:
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
    secrets: list[PersistentAgentSecret],
) -> tuple[Optional[bytes], Optional[dict[str, list[str]]], list[dict[str, str]]]:
    """Build the encrypted secret payload for a browser task plus any invalid rows."""
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
