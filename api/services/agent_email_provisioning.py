from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from api.agent.comms.email_providers import EMAIL_OAUTH_PROVIDER_DEFAULTS
from api.models import (
    AgentEmailAccount,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)
from api.services.agent_email_connection import (
    validate_agent_imap_connection,
    validate_agent_smtp_connection,
)
from api.services.persistent_agents import ensure_default_agent_email_endpoint


@dataclass(frozen=True)
class AgentEmailConnectionResult:
    smtp_ok: bool | None
    imap_ok: bool | None
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def configure_custom_agent_email(
    agent: PersistentAgent,
    *,
    address: str,
    password: str | None,
    smtp_host: str,
    smtp_port: int,
    smtp_security: str,
    imap_host: str,
    imap_port: int,
    imap_security: str,
) -> AgentEmailAccount:
    account = ensure_agent_email_account(agent, address)
    account.connection_mode = AgentEmailAccount.ConnectionMode.CUSTOM
    account.smtp_host = str(smtp_host or "").strip()
    account.smtp_port = _port(smtp_port, "smtp_port")
    account.smtp_security = _choice(
        smtp_security,
        "smtp_security",
        {choice[0] for choice in AgentEmailAccount.SmtpSecurity.choices},
    )
    account.smtp_auth = AgentEmailAccount.AuthMode.LOGIN
    account.smtp_username = account.endpoint.address
    if password is not None:
        account.set_smtp_password(password)
        account.set_imap_password(password)
    account.imap_host = str(imap_host or "").strip()
    account.imap_port = _port(imap_port, "imap_port")
    account.imap_security = _choice(
        imap_security,
        "imap_security",
        {choice[0] for choice in AgentEmailAccount.ImapSecurity.choices},
    )
    account.imap_auth = AgentEmailAccount.ImapAuthMode.LOGIN
    account.imap_username = account.endpoint.address
    account.imap_folder = "INBOX"
    account.imap_idle_enabled = True
    account.is_outbound_enabled = False
    account.is_inbound_enabled = False
    account.connection_last_ok_at = None
    account.connection_error = ""
    account.full_clean()
    account.save()
    return account


def prepare_oauth_agent_email(
    agent: PersistentAgent,
    *,
    address: str,
    provider: str,
) -> AgentEmailAccount:
    provider_key = str(provider or "").strip().lower()
    defaults = EMAIL_OAUTH_PROVIDER_DEFAULTS.get(provider_key)
    if defaults is None:
        raise ValidationError({"provider": "OAuth provider must be gmail, microsoft, or outlook."})

    account = ensure_agent_email_account(agent, address)
    account.connection_mode = AgentEmailAccount.ConnectionMode.OAUTH2
    account.smtp_auth = AgentEmailAccount.AuthMode.OAUTH2
    account.imap_auth = AgentEmailAccount.ImapAuthMode.OAUTH2
    account.smtp_username = account.endpoint.address
    account.imap_username = account.endpoint.address
    account.is_outbound_enabled = False
    account.is_inbound_enabled = False
    account.connection_last_ok_at = None
    account.connection_error = ""
    for key, value in defaults.items():
        setattr(account, key, value)
    account.full_clean()
    account.save()
    return account


def test_and_enable_agent_email(
    account: AgentEmailAccount,
    *,
    enable_outbound: bool,
    enable_inbound: bool,
) -> AgentEmailConnectionResult:
    smtp_ok: bool | None = None
    imap_ok: bool | None = None
    errors: list[str] = []

    if enable_outbound:
        smtp_ok, smtp_error = validate_agent_smtp_connection(account)
        if not smtp_ok:
            errors.append(f"SMTP test failed: {smtp_error}")
    if enable_inbound:
        imap_ok, imap_error = validate_agent_imap_connection(account)
        if not imap_ok:
            errors.append(f"IMAP test failed: {imap_error}")

    account.is_outbound_enabled = bool(enable_outbound and smtp_ok)
    account.is_inbound_enabled = bool(enable_inbound and imap_ok)
    any_connection_ok = smtp_ok is True or imap_ok is True
    account.connection_last_ok_at = timezone.now() if any_connection_ok else None
    account.connection_error = "; ".join(errors)
    account.full_clean()
    account.save(
        update_fields=[
            "is_outbound_enabled",
            "is_inbound_enabled",
            "connection_last_ok_at",
            "connection_error",
            "updated_at",
        ]
    )
    return AgentEmailConnectionResult(
        smtp_ok=smtp_ok,
        imap_ok=imap_ok,
        errors=tuple(errors),
    )


def ensure_agent_email_account(agent: PersistentAgent, address: str) -> AgentEmailAccount:
    normalized_address = _normalize_email_address(address)
    with transaction.atomic():
        if settings.ENABLE_DEFAULT_AGENT_EMAIL:
            ensure_default_agent_email_endpoint(agent, is_primary=False)

        endpoint = (
            PersistentAgentCommsEndpoint.objects.select_for_update()
            .filter(channel=CommsChannel.EMAIL, address__iexact=normalized_address)
            .first()
        )
        if endpoint and endpoint.owner_agent_id not in (None, agent.id):
            raise ValidationError({"address": "That email address is already assigned to another agent."})
        if endpoint is None:
            endpoint = PersistentAgentCommsEndpoint.objects.create(
                owner_agent=agent,
                channel=CommsChannel.EMAIL,
                address=normalized_address,
                is_primary=False,
            )
        else:
            endpoint.owner_agent = agent
            endpoint.address = normalized_address

        agent.comms_endpoints.filter(channel=CommsChannel.EMAIL, is_primary=True).exclude(
            id=endpoint.id
        ).update(is_primary=False)
        endpoint.is_primary = True
        endpoint.save(update_fields=["owner_agent", "address", "is_primary"])
        account, _created = AgentEmailAccount.objects.get_or_create(
            endpoint=endpoint,
            defaults={"imap_idle_enabled": True},
        )
    return account


def _normalize_email_address(address: str) -> str:
    raw_address = str(address or "").strip()
    try:
        validate_email(raw_address)
    except ValidationError as exc:
        raise ValidationError({"address": "A valid mailbox address is required."}) from exc
    normalized = PersistentAgentCommsEndpoint.normalize_address(CommsChannel.EMAIL, raw_address)
    if not normalized:
        raise ValidationError({"address": "A valid mailbox address is required."})
    return normalized


def _port(raw_value: int, field_name: str) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "A valid port is required."}) from exc
    if value < 1 or value > 65535:
        raise ValidationError({field_name: "Port must be between 1 and 65535."})
    return value


def _choice(raw_value: str, field_name: str, choices: set[str]) -> str:
    value = str(raw_value or "").strip().lower()
    if value not in choices:
        raise ValidationError({field_name: f"Choose one of: {', '.join(sorted(choices))}."})
    return value
