from typing import Optional

from django.core.exceptions import ValidationError

from api.domain_validation import DomainPatternValidator
from api.models import GlobalSecret, PersistentAgent, PersistentAgentSecret


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


def resolve_global_secret_owner_for_agent(agent: PersistentAgent):
    if agent.organization_id:
        return None, agent.organization
    return agent.user, None


def global_secrets_queryset_for_agent(agent: PersistentAgent):
    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    if owner_org is not None:
        return GlobalSecret.objects.filter(organization=owner_org)
    return GlobalSecret.objects.filter(user=owner_user, organization__isnull=True)


def ensure_global_secret_capacity_for_agent(agent: PersistentAgent, additional_count: int = 1) -> None:
    if additional_count <= 0:
        return

    global_secret_count = global_secrets_queryset_for_agent(agent).count()
    if global_secret_count + additional_count > GlobalSecret.MAX_GLOBAL_SECRETS_PER_OWNER:
        raise ValidationError(
            {"__all__": [f"Maximum {GlobalSecret.MAX_GLOBAL_SECRETS_PER_OWNER} global secrets allowed."]}
        )


def build_global_secret_from_agent_secret(secret: PersistentAgentSecret) -> GlobalSecret:
    owner_user, owner_org = resolve_global_secret_owner_for_agent(secret.agent)
    return GlobalSecret(
        user=owner_user,
        organization=owner_org,
        name=secret.name,
        description=secret.description,
        secret_type=secret.secret_type,
        domain_pattern=secret.domain_pattern,
        key=secret.key,
        encrypted_value=secret.encrypted_value,
    )


def _global_secret_scope_queryset(secret: GlobalSecret):
    if secret.organization_id:
        qs = GlobalSecret.objects.filter(organization=secret.organization)
    else:
        qs = GlobalSecret.objects.filter(user=secret.user, organization__isnull=True)
    if secret.pk:
        qs = qs.exclude(pk=secret.pk)
    return qs.filter(secret_type=secret.secret_type, domain_pattern=secret.domain_pattern)


def _validate_global_secret_uniqueness(secret: GlobalSecret) -> None:
    errors = {}
    scope_qs = _global_secret_scope_queryset(secret)

    if scope_qs.filter(name=secret.name).exists():
        errors["name"] = ["A global secret with that name already exists in this scope."]
    if secret.key and scope_qs.filter(key=secret.key).exists():
        errors["key"] = ["A global secret with that key already exists in this scope."]

    if errors:
        raise ValidationError(errors)


def validate_agent_secret_globalization(secret: PersistentAgentSecret) -> GlobalSecret:
    if not secret.encrypted_value:
        raise ValidationError({"value": ["Secret value is required."]})

    global_secret = build_global_secret_from_agent_secret(secret)
    _validate_global_secret_uniqueness(global_secret)
    global_secret.full_clean(validate_unique=False, validate_constraints=False)
    return global_secret


def move_agent_secret_to_global(secret: PersistentAgentSecret) -> GlobalSecret:
    ensure_global_secret_capacity_for_agent(secret.agent)
    global_secret = validate_agent_secret_globalization(secret)
    global_secret.save()
    secret.delete()
    return global_secret
