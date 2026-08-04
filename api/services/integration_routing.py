"""Owner-scoped native-over-Pipedream routing policy."""

import logging
from dataclasses import dataclass
from typing import Any

from api.models import GlobalSecret, NativeIntegrationRoutingLock, PersistentAgent
from api.pipedream_app_utils import normalize_app_slug

logger = logging.getLogger(__name__)

GOOGLE_DRIVE_PROVIDER_KEY = "google_drive"
GOOGLE_DRIVE_SECRET_KEYS = ("native_google_drive", "native_google_sheets")
NATIVE_PROVIDER_PIPEDREAM_APP_SLUGS = {
    GOOGLE_DRIVE_PROVIDER_KEY: ("google_sheets", "google_drive"),
}

PIPEDREAM_APP_SUPERSEDED_CODE = "pipedream_app_superseded"
ROUTING_STATUS_AVAILABLE = "available"
ROUTING_STATUS_SUPERSEDED = "superseded"


class PipedreamAppSupersededError(ValueError):
    def __init__(self, app_slug: str, provider_key: str, message: str):
        super().__init__(message)
        self.app_slug = app_slug
        self.provider_key = provider_key
        self.code = PIPEDREAM_APP_SUPERSEDED_CODE

    def to_dict(self) -> dict[str, str]:
        return {
            "error": str(self),
            "code": self.code,
            "app_slug": self.app_slug,
            "replacement_provider_key": self.provider_key,
        }

    def to_tool_result(self) -> dict[str, str]:
        return {
            "status": "error",
            "code": self.code,
            "message": str(self),
            "app_slug": self.app_slug,
            "replacement_provider_key": self.provider_key,
        }


@dataclass(frozen=True)
class PipedreamAppRoutingStatus:
    app_slug: str
    routing_status: str = ROUTING_STATUS_AVAILABLE
    routing_message: str = ""
    superseded_by_provider_key: str = ""
    native_connected: bool = False

    @property
    def superseded(self) -> bool:
        return self.routing_status == ROUTING_STATUS_SUPERSEDED

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "routing_status": self.routing_status,
            "routing_message": self.routing_message,
            "superseded_by_provider_key": self.superseded_by_provider_key or None,
        }


def _routing_lock_queryset(owner_user=None, owner_org=None):
    queryset = NativeIntegrationRoutingLock.objects.all()
    if owner_org is not None:
        return queryset.filter(organization=owner_org, user__isnull=True)
    if owner_user is not None:
        return queryset.filter(user=owner_user, organization__isnull=True)
    return queryset.none()


def _native_google_is_connected(owner_user=None, owner_org=None) -> bool:
    queryset = GlobalSecret.objects.filter(
        secret_type=GlobalSecret.SecretType.INTEGRATION,
        domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
        key__in=GOOGLE_DRIVE_SECRET_KEYS,
    )
    if owner_org is not None:
        return queryset.filter(organization=owner_org, user__isnull=True).exists()
    if owner_user is not None:
        return queryset.filter(user=owner_user, organization__isnull=True).exists()
    return False


def ensure_native_integration_routing_lock(provider_key: str, owner_user=None, owner_org=None):
    normalized_provider = str(provider_key or "").strip()
    if normalized_provider not in NATIVE_PROVIDER_PIPEDREAM_APP_SLUGS:
        return None
    if bool(owner_user) == bool(owner_org):
        raise ValueError("Native integration routing locks require exactly one owner.")
    owner_fields = (
        {"organization": owner_org, "user": None}
        if owner_org is not None
        else {"organization": None, "user": owner_user}
    )
    lock, _created = NativeIntegrationRoutingLock.objects.get_or_create(
        provider_key=normalized_provider,
        **owner_fields,
    )
    return lock


def get_superseded_pipedream_app_slugs(owner_user=None, owner_org=None) -> set[str]:
    locked_providers = set(
        _routing_lock_queryset(owner_user, owner_org).values_list("provider_key", flat=True)
    )
    if _native_google_is_connected(owner_user, owner_org):
        locked_providers.add(GOOGLE_DRIVE_PROVIDER_KEY)
    return {
        app_slug
        for provider_key in locked_providers
        for app_slug in NATIVE_PROVIDER_PIPEDREAM_APP_SLUGS.get(provider_key, ())
    }


def get_superseded_pipedream_app_slugs_for_owner_id(owner_scope: str, owner_id: object) -> set[str]:
    if not owner_id:
        return set()
    if str(owner_scope) == "organization":
        lock_filter = {"organization_id": owner_id, "user__isnull": True}
        secret_filter = {"organization_id": owner_id, "user__isnull": True}
    else:
        lock_filter = {"user_id": owner_id, "organization__isnull": True}
        secret_filter = {"user_id": owner_id, "organization__isnull": True}

    locked_providers = set(
        NativeIntegrationRoutingLock.objects.filter(**lock_filter).values_list("provider_key", flat=True)
    )
    if GlobalSecret.objects.filter(
        secret_type=GlobalSecret.SecretType.INTEGRATION,
        domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
        key__in=GOOGLE_DRIVE_SECRET_KEYS,
        **secret_filter,
    ).exists():
        locked_providers.add(GOOGLE_DRIVE_PROVIDER_KEY)
    return {
        app_slug
        for provider_key in locked_providers
        for app_slug in NATIVE_PROVIDER_PIPEDREAM_APP_SLUGS.get(provider_key, ())
    }


def get_pipedream_app_routing_status(
    app_slug: object,
    owner_user=None,
    owner_org=None,
) -> PipedreamAppRoutingStatus:
    normalized_slug = normalize_app_slug(app_slug)
    if not normalized_slug:
        return PipedreamAppRoutingStatus(app_slug="")

    for provider_key, app_slugs in NATIVE_PROVIDER_PIPEDREAM_APP_SLUGS.items():
        if normalized_slug not in app_slugs:
            continue
        native_connected = (
            provider_key == GOOGLE_DRIVE_PROVIDER_KEY
            and _native_google_is_connected(owner_user, owner_org)
        )
        has_lock = _routing_lock_queryset(owner_user, owner_org).filter(provider_key=provider_key).exists()
        if not native_connected and not has_lock:
            break
        if native_connected:
            message = (
                "Superseded by native Google Drive. Use the native integration for Google Sheets and Drive."
            )
        else:
            message = (
                "Native Google Drive is the preferred route. Reconnect it to use Google Sheets and Drive."
            )
        return PipedreamAppRoutingStatus(
            app_slug=normalized_slug,
            routing_status=ROUTING_STATUS_SUPERSEDED,
            routing_message=message,
            superseded_by_provider_key=provider_key,
            native_connected=native_connected,
        )
    return PipedreamAppRoutingStatus(app_slug=normalized_slug)


def get_agent_owner(agent: PersistentAgent) -> tuple[Any | None, Any | None]:
    if agent.organization_id:
        return None, agent.organization
    return agent.user, None


def get_pipedream_app_routing_status_for_agent(
    agent: PersistentAgent,
    app_slug: object,
) -> PipedreamAppRoutingStatus:
    owner_user, owner_org = get_agent_owner(agent)
    return get_pipedream_app_routing_status(app_slug, owner_user, owner_org)


def assert_pipedream_app_available_for_agent(
    agent: PersistentAgent,
    app_slug: object,
    *,
    entry_point: str,
) -> None:
    status = get_pipedream_app_routing_status_for_agent(agent, app_slug)
    if not status.superseded:
        return
    owner_scope = "organization" if agent.organization_id else "user"
    owner_id = agent.organization_id or agent.user_id
    logger.info(
        "Blocked superseded Pipedream app owner_scope=%s owner_id=%s agent_id=%s app_slug=%s entry_point=%s",
        owner_scope,
        owner_id,
        agent.id,
        status.app_slug,
        entry_point,
    )
    raise PipedreamAppSupersededError(
        status.app_slug,
        status.superseded_by_provider_key,
        status.routing_message,
    )


def superseded_pipedream_tool_result_for_agent(
    agent: PersistentAgent,
    app_slug: object,
    *,
    entry_point: str,
) -> dict[str, str] | None:
    try:
        assert_pipedream_app_available_for_agent(agent, app_slug, entry_point=entry_point)
    except PipedreamAppSupersededError as exc:
        return exc.to_tool_result()
    return None
