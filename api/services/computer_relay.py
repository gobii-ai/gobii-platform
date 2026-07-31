import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from packaging.version import InvalidVersion, Version
from opentelemetry import metrics
from waffle import get_waffle_flag_model

from api.agent.system_skills.service import enable_system_skills
from api.models import (
    ComputerDevice,
    ComputerDeviceApp,
    ComputerDeviceAssignment,
    ComputerDeviceCredential,
    ComputerPairingSession,
    ComputerRelayArtifact,
    MCPServerConfig,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentMCPServer,
    PersistentAgentSystemSkillState,
)
from api.services.organization_permissions import ORG_AGENT_CONFIG_AUTHORITY_ROLES
from config.redis_client import get_redis_client
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)
_computer_meter = metrics.get_meter("gobii.computer_relay")
computer_relay_events = _computer_meter.create_counter(
    "gobii.computer_relay.events",
    description="Computer pairing, presence, discovery, and relay outcomes",
)
computer_relay_call_latency = _computer_meter.create_histogram(
    "gobii.computer_relay.call_latency",
    unit="s",
    description="Computer relay MCP call latency",
)
computer_relay_discovery_latency = _computer_meter.create_histogram(
    "gobii.computer_relay.discovery_latency",
    unit="s",
    description="Computer app manifest synchronization latency",
)
computer_relay_active_sockets = _computer_meter.create_up_down_counter(
    "gobii.computer_relay.active_sockets",
    description="Currently active computer relay WebSocket connections",
)

COMPUTER_CPP_WAFFLE_FLAG = "computer_cpp_integration"
COMPUTER_SYSTEM_SKILL_KEY = "computer"
RELAY_ACCESS_TOKEN_SALT = "computer-relay-access-v1"
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_PRESENCE_KEY_PREFIX = "computer-relay:presence:"
_REQUEST_LOCK_PREFIX = "computer-relay:request-lock:"
_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ComputerRelayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[computer:{code}] {message}")


def record_computer_relay_event(
    event: str,
    *,
    user_id=None,
    analytics_event: AnalyticsEvent | None = None,
    analytics_source: AnalyticsSource = AnalyticsSource.CONSOLE,
    **attributes,
) -> None:
    event_fields = {"event": event, **{key: value for key, value in attributes.items() if value is not None}}
    dimensions = {
        "event": event,
        **{
            key: value
            for key, value in attributes.items()
            if key in {"error_type", "outcome", "platform"} and value is not None
        },
    }
    computer_relay_events.add(1, dimensions)
    logger.info("Computer relay event", extra=event_fields)
    if user_id is not None and analytics_event is not None:
        Analytics.track_event(
            user_id,
            analytics_event,
            analytics_source,
            {key: value for key, value in attributes.items() if value is not None},
        )


def computer_rate_limited(key: str, *, limit: int, window_seconds: int) -> bool:
    if cache.add(key, 1, timeout=window_seconds):
        return False
    try:
        return int(cache.incr(key)) > limit
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
        return False


def _secret_digest(value: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def pairing_user_code_matches(pairing: ComputerPairingSession, user_code: str) -> bool:
    return hmac.compare_digest(
        pairing.user_code_digest,
        _secret_digest(str(user_code or "").strip().upper()),
    )


def pairing_device_code_matches(pairing: ComputerPairingSession, device_code: str) -> bool:
    return hmac.compare_digest(
        pairing.device_code_digest,
        _secret_digest(str(device_code or "")),
    )


def computer_cpp_enabled_for_user(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    try:
        flag = get_waffle_flag_model().objects.get(name=COMPUTER_CPP_WAFFLE_FLAG)
        return bool(flag.is_active_for_user(user))
    except get_waffle_flag_model().DoesNotExist:
        return False
    except (AttributeError, TypeError, ValueError):
        logger.warning("Unable to evaluate computer.cpp flag for user %s", getattr(user, "id", None))
        return False


def computer_client_version_supported(client_version: str) -> bool:
    try:
        return Version(client_version) >= Version(settings.COMPUTER_CPP_MINIMUM_CLIENT_VERSION)
    except InvalidVersion:
        return False


def _normalize_manifest(raw_manifest: object) -> list[dict[str, str]]:
    if not isinstance(raw_manifest, list):
        raise ValueError("apps must be a list")
    if len(raw_manifest) > 50:
        raise ValueError("A device may advertise at most 50 apps")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_manifest:
        if not isinstance(raw, dict):
            raise ValueError("Each app must be an object")
        app_key = slugify(str(raw.get("key") or raw.get("name") or ""))[:80]
        display_name = str(raw.get("display_name") or raw.get("name") or app_key).strip()[:128]
        schema_hash = str(raw.get("schema_sha256") or "").strip().lower()
        app_type = str(raw.get("type") or ComputerDeviceApp.AppType.CUSTOM).strip().lower()
        if not app_key or app_key in seen:
            raise ValueError("App keys must be unique and non-empty")
        if not display_name:
            raise ValueError(f"App '{app_key}' requires a display name")
        if not _DIGEST_RE.fullmatch(schema_hash):
            raise ValueError(f"App '{app_key}' requires a SHA-256 schema digest")
        if app_type not in ComputerDeviceApp.AppType.values:
            raise ValueError(f"App '{app_key}' has an invalid type")
        seen.add(app_key)
        normalized.append(
            {
                "key": app_key,
                "display_name": display_name,
                "schema_sha256": schema_hash,
                "type": app_type,
            }
        )
    return normalized


def create_pairing_session(payload: dict[str, Any]) -> tuple[ComputerPairingSession, str, str]:
    machine_identifier = str(payload.get("machine_id") or "").strip()
    display_name = str(payload.get("display_name") or "").strip()[:128]
    platform = str(payload.get("platform") or "").strip().lower()
    architecture = str(payload.get("architecture") or "").strip().lower()[:32]
    client_version = str(payload.get("client_version") or "").strip()[:32]
    try:
        protocol_version = int(payload.get("protocol_version"))
    except (TypeError, ValueError):
        raise ValueError("protocol_version must be an integer")

    if not machine_identifier or len(machine_identifier) > 512:
        raise ValueError("machine_id is required")
    if not display_name:
        raise ValueError("display_name is required")
    if platform not in ComputerDevice.Platform.values:
        raise ValueError("platform must be macos or windows")
    if not architecture:
        raise ValueError("architecture is required")
    if not client_version:
        raise ValueError("client_version is required")

    manifest = _normalize_manifest(payload.get("apps", []))
    device_code = secrets.token_urlsafe(32)
    raw_user_code = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))
    user_code = f"{raw_user_code[:4]}-{raw_user_code[4:]}"
    pairing = ComputerPairingSession.objects.create(
        device_code_digest=_secret_digest(device_code),
        user_code_digest=_secret_digest(user_code.upper()),
        machine_identifier_digest=_secret_digest(machine_identifier),
        display_name=display_name,
        platform=platform,
        architecture=architecture,
        client_version=client_version,
        protocol_version=protocol_version,
        app_manifest=manifest,
        expires_at=timezone.now() + timedelta(seconds=settings.COMPUTER_CPP_PAIRING_TTL_SECONDS),
    )
    record_computer_relay_event("pairing_started", platform=platform)
    return pairing, device_code, user_code


def manageable_agents_for_user(user):
    organization_ids = OrganizationMembership.objects.filter(
        user=user,
        status=OrganizationMembership.OrgStatus.ACTIVE,
        role__in=ORG_AGENT_CONFIG_AUTHORITY_ROLES,
    ).values_list("org_id", flat=True)
    return (
        PersistentAgent.objects.non_eval()
        .alive()
        .filter(is_active=True, deleted_at__isnull=True)
        .filter(
            Q(user=user, organization__isnull=True)
            | Q(organization_id__in=organization_ids)
        )
        .select_related("organization")
        .order_by("organization__name", "name")
    )


def user_can_assign_agent(user, agent: PersistentAgent) -> bool:
    if (
        agent.is_deleted
        or agent.deleted_at is not None
        or not agent.is_active
        or agent.execution_environment == "eval"
    ):
        return False
    if agent.organization_id is None:
        return agent.user_id == user.id
    return OrganizationMembership.objects.filter(
        user=user,
        org_id=agent.organization_id,
        status=OrganizationMembership.OrgStatus.ACTIVE,
        role__in=ORG_AGENT_CONFIG_AUTHORITY_ROLES,
    ).exists()


def approve_pairing(
    pairing: ComputerPairingSession,
    *,
    user,
    user_code: str,
    agent: PersistentAgent,
    selected_app_keys: list[str],
) -> ComputerPairingSession:
    if not computer_cpp_enabled_for_user(user):
        raise PermissionError("Computer connections are not enabled for this account")
    if not user_can_assign_agent(user, agent):
        raise PermissionError("You cannot configure the selected agent")

    selected = list(dict.fromkeys(str(value) for value in selected_app_keys))
    with transaction.atomic():
        pairing = ComputerPairingSession.objects.select_for_update().get(id=pairing.id)
        now = timezone.now()
        if pairing.status != ComputerPairingSession.Status.PENDING or pairing.expires_at <= now:
            raise ValueError("This pairing request is no longer active")
        if not pairing_user_code_matches(pairing, user_code):
            raise ValueError("The verification code does not match")
        manifest_keys = {entry["key"] for entry in pairing.app_manifest}
        if not set(selected).issubset(manifest_keys):
            raise ValueError("One or more selected apps are not advertised by this device")

        pairing.status = ComputerPairingSession.Status.APPROVED
        pairing.approved_by = user
        pairing.selected_agent = agent
        pairing.selected_app_keys = selected
        pairing.approved_at = now
        pairing.save(
            update_fields=[
                "status",
                "approved_by",
                "selected_agent",
                "selected_app_keys",
                "approved_at",
            ]
        )
    record_computer_relay_event(
        "pairing_approved",
        user_id=user.id,
        analytics_event=AnalyticsEvent.COMPUTER_PAIRING_APPROVED,
        pairing_id=str(pairing.id),
        agent_id=str(agent.id),
        app_count=len(selected),
        platform=pairing.platform,
    )
    return pairing


def deny_pairing(pairing: ComputerPairingSession) -> None:
    with transaction.atomic():
        pairing = ComputerPairingSession.objects.select_for_update().get(id=pairing.id)
        if pairing.status != ComputerPairingSession.Status.PENDING:
            raise ValueError("This pairing request is no longer pending")
        pairing.status = ComputerPairingSession.Status.DENIED
        pairing.denied_at = timezone.now()
        pairing.save(update_fields=["status", "denied_at"])
    record_computer_relay_event("pairing_denied", platform=pairing.platform)


def _refresh_token_value(credential: ComputerDeviceCredential, secret: str) -> str:
    return f"{credential.id}.{secret}"


def _create_refresh_credential(
    device: ComputerDevice,
    *,
    family_id: uuid.UUID | None = None,
) -> tuple[ComputerDeviceCredential, str]:
    secret = secrets.token_urlsafe(48)
    credential = ComputerDeviceCredential.objects.create(
        device=device,
        family_id=family_id or uuid.uuid4(),
        generation=device.credential_generation,
        token_digest=_secret_digest(secret),
        expires_at=timezone.now() + timedelta(seconds=settings.COMPUTER_CPP_REFRESH_TOKEN_TTL_SECONDS),
    )
    return credential, _refresh_token_value(credential, secret)


def issue_relay_access_token(device: ComputerDevice) -> str:
    expires_at = timezone.now() + timedelta(seconds=settings.COMPUTER_CPP_ACCESS_TOKEN_TTL_SECONDS)
    return signing.dumps(
        {
            "device_id": str(device.id),
            "generation": device.credential_generation,
            "aud": "computer-relay",
            "exp": int(expires_at.timestamp()),
        },
        salt=RELAY_ACCESS_TOKEN_SALT,
        compress=True,
    )


def authenticate_relay_access_token(token: str) -> ComputerDevice:
    try:
        payload = signing.loads(
            token,
            salt=RELAY_ACCESS_TOKEN_SALT,
            max_age=settings.COMPUTER_CPP_ACCESS_TOKEN_TTL_SECONDS,
        )
    except signing.BadSignature as exc:
        raise PermissionError("Invalid or expired relay token") from exc
    if payload.get("aud") != "computer-relay":
        raise PermissionError("Invalid relay token audience")
    if int(payload.get("exp") or 0) <= int(timezone.now().timestamp()):
        raise PermissionError("Invalid or expired relay token")
    return validate_relay_device(
        payload.get("device_id"),
        payload.get("generation"),
    )


def validate_relay_device(device_id, credential_generation) -> ComputerDevice:
    device = (
        ComputerDevice.objects.select_related("owner")
        .filter(id=device_id, revoked_at__isnull=True)
        .first()
    )
    if device is None or device.credential_generation != credential_generation:
        raise PermissionError("Relay credentials have been revoked")
    if not computer_cpp_enabled_for_user(device.owner):
        raise PermissionError("Computer connections are not enabled for this account")
    if device.protocol_version != settings.COMPUTER_CPP_RELAY_PROTOCOL_VERSION:
        raise PermissionError("Relay protocol update required")
    if not computer_client_version_supported(device.client_version):
        raise PermissionError("Computer application update required")
    return device


def _managed_server_name(device: ComputerDevice, app: ComputerDeviceApp) -> str:
    app_part = (slugify(app.app_key)[:38] or "app").replace("-", "_")
    return f"computer_{device.id.hex[:8]}_{app_part}"[:64]


def _disable_app_config(app: ComputerDeviceApp) -> None:
    config = app.mcp_server_config
    if config is None:
        return
    PersistentAgentEnabledTool.objects.filter(server_config=config).delete()
    PersistentAgentMCPServer.objects.filter(server_config=config).delete()
    if config.is_active:
        config.is_active = False
        config.save(update_fields=["is_active", "updated_at"])
    _refresh_mcp_server(config.id)


def _refresh_mcp_server(config_id) -> None:
    from api.agent.tools.mcp_manager import get_mcp_manager

    transaction.on_commit(lambda: get_mcp_manager().refresh_server(str(config_id)))


def _ensure_app_config(
    device: ComputerDevice,
    app: ComputerDeviceApp,
    assignment: ComputerDeviceAssignment,
) -> MCPServerConfig:
    agent = assignment.agent
    scope = MCPServerConfig.Scope.ORGANIZATION if agent.organization_id else MCPServerConfig.Scope.USER
    defaults = {
        "scope": scope,
        "organization": agent.organization if agent.organization_id else None,
        "user": None if agent.organization_id else device.owner,
        "name": _managed_server_name(device, app),
        "display_name": f"{device.display_name} · {app.display_name}"[:128],
        "description": f"Runs {app.display_name} tools on {device.display_name}.",
        "transport": MCPServerConfig.Transport.COMPUTER_RELAY,
        "auth_method": MCPServerConfig.AuthMethod.NONE,
        "command": "",
        "command_args": [],
        "url": "",
        "metadata": {
            "computer_device_id": str(device.id),
            "computer_device_app_id": str(app.id),
            "schema_sha256": app.approved_schema_hash,
        },
        "is_active": True,
    }
    config = app.mcp_server_config
    if config is None:
        config = MCPServerConfig.objects.create(**defaults)
        app.mcp_server_config = config
        app.save(update_fields=["mcp_server_config", "updated_at"])
    else:
        for key, value in defaults.items():
            setattr(config, key, value)
        config.save(update_fields=[*defaults.keys(), "updated_at"])

    PersistentAgentMCPServer.objects.update_or_create(
        agent=agent,
        server_config=config,
    )
    _refresh_mcp_server(config.id)
    return config


def _reconcile_app_configs(
    device: ComputerDevice,
    apps: list[ComputerDeviceApp],
    assignment: ComputerDeviceAssignment | None,
) -> None:
    for app in apps:
        if (
            assignment
            and app.is_available
            and app.approval_state == ComputerDeviceApp.ApprovalState.APPROVED
            and app.approved_schema_hash == app.reported_schema_hash
        ):
            _ensure_app_config(device, app, assignment)
        else:
            _disable_app_config(app)


def _active_assignment(device: ComputerDevice) -> ComputerDeviceAssignment | None:
    return (
        ComputerDeviceAssignment.objects.select_related("agent")
        .filter(device=device, revoked_at__isnull=True)
        .first()
    )


def _sync_agent_skill(agent: PersistentAgent) -> None:
    has_assignment = ComputerDeviceAssignment.objects.filter(
        agent=agent,
        revoked_at__isnull=True,
        device__revoked_at__isnull=True,
    ).exists()
    if has_assignment:
        enable_system_skills(agent, [COMPUTER_SYSTEM_SKILL_KEY])
    else:
        PersistentAgentSystemSkillState.objects.filter(
            agent=agent,
            skill_key=COMPUTER_SYSTEM_SKILL_KEY,
        ).update(is_enabled=False)


def _queue_agent_resume(agent_id: uuid.UUID | str) -> None:
    from api.agent.tasks.process_events import process_agent_events_task

    transaction.on_commit(lambda: process_agent_events_task.delay(str(agent_id)))


def assign_device(
    device: ComputerDevice,
    agent: PersistentAgent,
    *,
    granted_by,
) -> ComputerDeviceAssignment:
    if device.owner_id != granted_by.id or not user_can_assign_agent(granted_by, agent):
        raise PermissionError("You cannot assign this computer to the selected agent")

    reassigned = False
    with transaction.atomic():
        device = ComputerDevice.objects.select_for_update().get(id=device.id)
        if not user_can_assign_agent(granted_by, agent):
            raise PermissionError("You cannot assign this computer to the selected agent")
        existing = (
            ComputerDeviceAssignment.objects.select_related("agent")
            .filter(device=device)
            .first()
        )
        old_agent = existing.agent if existing and existing.revoked_at is None else None
        reassigned = bool(old_agent and old_agent.id != agent.id)

        if existing is None:
            assignment = ComputerDeviceAssignment.objects.create(
                device=device,
                agent=agent,
                organization=agent.organization,
                granted_by=granted_by,
            )
        else:
            if old_agent and old_agent.id != agent.id:
                PersistentAgentMCPServer.objects.filter(
                    agent=old_agent,
                    server_config__computer_device_app__device=device,
                ).delete()
                PersistentAgentEnabledTool.objects.filter(
                    agent=old_agent,
                    server_config__computer_device_app__device=device,
                ).delete()
            existing.agent = agent
            existing.organization = agent.organization
            existing.granted_by = granted_by
            existing.revoked_at = None
            existing.save(
                update_fields=[
                    "agent",
                    "organization",
                    "granted_by",
                    "revoked_at",
                    "updated_at",
                ]
            )
            assignment = existing

        _reconcile_app_configs(
            device,
            list(device.apps.select_related("mcp_server_config")),
            assignment,
        )

        _sync_agent_skill(agent)
        if old_agent and old_agent.id != agent.id:
            _sync_agent_skill(old_agent)
        _queue_agent_resume(agent.id)
    record_computer_relay_event(
        "computer_reassigned" if reassigned else "computer_assigned",
        user_id=granted_by.id,
        analytics_event=AnalyticsEvent.COMPUTER_REASSIGNED if reassigned else AnalyticsEvent.COMPUTER_ASSIGNED,
        device_id=str(device.id),
        agent_id=str(agent.id),
        organization_id=str(agent.organization_id) if agent.organization_id else None,
    )
    return assignment


def sync_device_manifest(
    device: ComputerDevice,
    manifest: object,
    *,
    initially_selected: set[str] | None = None,
    resume_agent: bool = False,
) -> list[ComputerDeviceApp]:
    started_at = time.monotonic()
    normalized = _normalize_manifest(manifest)
    now = timezone.now()
    selected = initially_selected or set()

    with transaction.atomic():
        ComputerDeviceApp.objects.filter(device=device).update(is_available=False)
        for entry in normalized:
            app = ComputerDeviceApp.objects.select_related("mcp_server_config").filter(
                device=device,
                app_key=entry["key"],
            ).first()
            if app is None:
                approved = entry["key"] in selected
                app = ComputerDeviceApp.objects.create(
                    device=device,
                    app_key=entry["key"],
                    display_name=entry["display_name"],
                    app_type=entry["type"],
                    reported_schema_hash=entry["schema_sha256"],
                    approved_schema_hash=entry["schema_sha256"] if approved else "",
                    approval_state=(
                        ComputerDeviceApp.ApprovalState.APPROVED
                        if approved
                        else ComputerDeviceApp.ApprovalState.PENDING
                    ),
                    is_available=True,
                    last_seen_at=now,
                )
            else:
                schema_changed = app.reported_schema_hash != entry["schema_sha256"]
                app.display_name = entry["display_name"]
                app.app_type = entry["type"]
                app.reported_schema_hash = entry["schema_sha256"]
                app.is_available = True
                app.last_seen_at = now
                if initially_selected is not None:
                    approved = entry["key"] in selected
                    app.approved_schema_hash = entry["schema_sha256"] if approved else ""
                    app.approval_state = (
                        ComputerDeviceApp.ApprovalState.APPROVED
                        if approved
                        else ComputerDeviceApp.ApprovalState.PENDING
                    )
                elif schema_changed and app.approved_schema_hash != entry["schema_sha256"]:
                    app.approval_state = ComputerDeviceApp.ApprovalState.PENDING
                app.save(
                    update_fields=[
                        "display_name",
                        "app_type",
                        "reported_schema_hash",
                        "approved_schema_hash",
                        "approval_state",
                        "is_available",
                        "last_seen_at",
                        "updated_at",
                    ]
                )

        assignment = _active_assignment(device)
        result = list(
            ComputerDeviceApp.objects.filter(device=device)
            .select_related("mcp_server_config")
            .order_by("display_name")
        )
        _reconcile_app_configs(device, result, assignment)
        if resume_agent and assignment:
            _queue_agent_resume(assignment.agent_id)
    computer_relay_discovery_latency.record(
        time.monotonic() - started_at,
        {"platform": device.platform},
    )
    record_computer_relay_event("manifest_synchronized", platform=device.platform)
    return result


def redeem_pairing(
    pairing: ComputerPairingSession,
    *,
    device_code: str,
) -> tuple[ComputerDevice, str, str]:
    pairing = ComputerPairingSession.objects.select_related(
        "approved_by",
        "selected_agent",
    ).get(id=pairing.id)
    now = timezone.now()
    if not pairing_device_code_matches(pairing, device_code):
        raise PermissionError("Invalid device code")
    if pairing.expires_at <= now:
        raise ComputerRelayError("expired", "Pairing request expired")
    if pairing.status == ComputerPairingSession.Status.PENDING:
        raise ComputerRelayError("authorization_pending", "Waiting for user approval", retryable=True)
    if pairing.status == ComputerPairingSession.Status.DENIED:
        raise ComputerRelayError("access_denied", "Pairing request was denied")
    if pairing.status == ComputerPairingSession.Status.REDEEMED:
        raise ComputerRelayError("already_redeemed", "Pairing credentials were already redeemed")
    if pairing.approved_by is None or pairing.selected_agent is None:
        raise ComputerRelayError("invalid_pairing", "Approved pairing is incomplete")
    if not computer_cpp_enabled_for_user(pairing.approved_by):
        raise PermissionError("Computer connections are not enabled for this account")

    replaced_existing_credentials = False
    with transaction.atomic():
        pairing = ComputerPairingSession.objects.select_for_update().get(id=pairing.id)
        if pairing.expires_at <= timezone.now():
            raise ComputerRelayError("expired", "Pairing request expired")
        if pairing.status != ComputerPairingSession.Status.APPROVED:
            raise ComputerRelayError(
                "already_redeemed",
                "Pairing credentials were already redeemed",
            )
        existing = ComputerDevice.objects.select_for_update().filter(
            machine_identifier_digest=pairing.machine_identifier_digest,
        ).first()
        if existing is not None and existing.owner_id != pairing.approved_by_id:
            raise PermissionError("This computer is already paired to another account")
        if existing is None:
            device = ComputerDevice.objects.create(
                owner=pairing.approved_by,
                machine_identifier_digest=pairing.machine_identifier_digest,
                display_name=pairing.display_name,
                platform=pairing.platform,
                architecture=pairing.architecture,
                client_version=pairing.client_version,
                protocol_version=pairing.protocol_version,
            )
        else:
            device = existing
            replaced_existing_credentials = True
            device.display_name = pairing.display_name
            device.platform = pairing.platform
            device.architecture = pairing.architecture
            device.client_version = pairing.client_version
            device.protocol_version = pairing.protocol_version
            device.revoked_at = None
            device.is_paused = False
            device.credential_generation += 1
            device.save(
                update_fields=[
                    "display_name",
                    "platform",
                    "architecture",
                    "client_version",
                    "protocol_version",
                    "revoked_at",
                    "is_paused",
                    "credential_generation",
                    "updated_at",
                ]
            )
            ComputerDeviceCredential.objects.filter(device=device, revoked_at__isnull=True).update(
                revoked_at=now
            )

        sync_device_manifest(
            device,
            pairing.app_manifest,
            initially_selected=set(pairing.selected_app_keys),
        )
        assign_device(device, pairing.selected_agent, granted_by=pairing.approved_by)
        _, refresh_token = _create_refresh_credential(device)
        pairing.status = ComputerPairingSession.Status.REDEEMED
        pairing.redeemed_at = now
        pairing.save(update_fields=["status", "redeemed_at"])
    if replaced_existing_credentials:
        send_device_control(device.id, "relay.close", {"code": "credentials_replaced"})
    record_computer_relay_event(
        "credential_redeemed",
        user_id=device.owner_id,
        analytics_event=AnalyticsEvent.COMPUTER_CREDENTIAL_REDEEMED,
        analytics_source=AnalyticsSource.API,
        device_id=str(device.id),
        platform=device.platform,
    )
    return device, refresh_token, issue_relay_access_token(device)


def rotate_refresh_token(raw_token: str) -> tuple[ComputerDevice, str, str]:
    try:
        token_id_raw, secret = raw_token.split(".", 1)
        token_id = uuid.UUID(token_id_raw)
    except (AttributeError, ValueError) as exc:
        raise PermissionError("Invalid refresh token") from exc

    reuse_detected = False
    result = None
    with transaction.atomic():
        credential = (
            ComputerDeviceCredential.objects.select_for_update()
            .select_related("device__owner")
            .filter(id=token_id)
            .first()
        )
        if credential is None or not hmac.compare_digest(credential.token_digest, _secret_digest(secret)):
            raise PermissionError("Invalid refresh token")
        device = credential.device
        now = timezone.now()
        if credential.consumed_at is not None:
            ComputerDeviceCredential.objects.filter(
                device=device,
                family_id=credential.family_id,
                revoked_at__isnull=True,
            ).update(revoked_at=now)
            device.credential_generation += 1
            device.save(update_fields=["credential_generation", "updated_at"])
            reuse_detected = True
        else:
            if (
                credential.revoked_at is not None
                or credential.expires_at <= now
                or credential.generation != device.credential_generation
                or device.revoked_at is not None
            ):
                raise PermissionError("Refresh token is expired or revoked")
            if not computer_cpp_enabled_for_user(device.owner):
                raise PermissionError("Computer connections are not enabled for this account")
            if computer_rate_limited(
                f"computer-token-refresh:{device.id}",
                limit=settings.COMPUTER_CPP_REFRESHES_PER_DEVICE_HOUR,
                window_seconds=3600,
            ):
                raise ComputerRelayError(
                    "rate_limited",
                    "Too many refresh attempts for this computer",
                    retryable=True,
                )

            replacement, refresh_token = _create_refresh_credential(
                device,
                family_id=credential.family_id,
            )
            credential.consumed_at = now
            credential.replaced_by = replacement
            credential.save(update_fields=["consumed_at", "replaced_by"])
            result = (device, refresh_token)
    if reuse_detected:
        send_device_control(device.id, "relay.close", {"code": "credentials_revoked"})
        record_computer_relay_event("refresh_reuse_detected")
        raise PermissionError("Refresh token reuse detected; pair this computer again")
    device, refresh_token = result
    record_computer_relay_event("refresh_succeeded", platform=device.platform)
    return device, refresh_token, issue_relay_access_token(device)


def approve_device_apps(device: ComputerDevice, selected: list[dict[str, str]]) -> None:
    requested = {
        str(item.get("app_key")): str(item.get("schema_sha256"))
        for item in selected
        if isinstance(item, dict)
    }
    with transaction.atomic():
        apps = list(device.apps.select_related("mcp_server_config"))
        known = {app.app_key for app in apps}
        if not set(requested).issubset(known):
            raise ValueError("One or more selected apps are unknown")
        assignment = _active_assignment(device)
        for app in apps:
            requested_hash = requested.get(app.app_key)
            if requested_hash is None:
                app.approval_state = ComputerDeviceApp.ApprovalState.DISABLED
                app.approved_schema_hash = ""
                app.save(update_fields=["approval_state", "approved_schema_hash", "updated_at"])
                continue
            if requested_hash != app.reported_schema_hash or not app.is_available:
                raise ValueError(f"App '{app.app_key}' schema is no longer current")
            app.approval_state = ComputerDeviceApp.ApprovalState.APPROVED
            app.approved_schema_hash = requested_hash
            app.save(update_fields=["approval_state", "approved_schema_hash", "updated_at"])
        _reconcile_app_configs(device, apps, assignment)
        if assignment:
            _queue_agent_resume(assignment.agent_id)
    record_computer_relay_event(
        "app_approval_changed",
        user_id=device.owner_id,
        analytics_event=AnalyticsEvent.COMPUTER_APPS_APPROVAL_CHANGED,
        device_id=str(device.id),
        approved_app_count=len(requested),
    )


def update_device_properties(
    device: ComputerDevice,
    *,
    display_name: str | None = None,
    paused: bool | None = None,
) -> ComputerDevice:
    pause_changed = paused is not None and bool(paused) != device.is_paused
    with transaction.atomic():
        update_fields = []
        if display_name is not None and display_name != device.display_name:
            device.display_name = display_name
            update_fields.append("display_name")
        if pause_changed:
            device.is_paused = bool(paused)
            update_fields.append("is_paused")
        if update_fields:
            device.save(update_fields=[*update_fields, "updated_at"])

        if "display_name" in update_fields:
            for app in device.apps.select_related("mcp_server_config"):
                config = app.mcp_server_config
                if config is None:
                    continue
                config.display_name = f"{device.display_name} · {app.display_name}"[:128]
                config.description = f"Runs {app.display_name} tools on {device.display_name}."
                config.save(update_fields=["display_name", "description", "updated_at"])

        assignment = _active_assignment(device)
        if assignment and assignment.agent_id and update_fields:
            _queue_agent_resume(assignment.agent_id)
    if pause_changed:
        send_device_control(device.id, "relay.state", {"paused": device.is_paused})
        record_computer_relay_event(
            "computer_paused" if device.is_paused else "computer_resumed",
            user_id=device.owner_id,
            analytics_event=AnalyticsEvent.COMPUTER_PAUSED if device.is_paused else AnalyticsEvent.COMPUTER_RESUMED,
            device_id=str(device.id),
        )
    return device


def revoke_assignment(device: ComputerDevice, *, revoked_by=None) -> None:
    assignment = _active_assignment(device)
    if assignment is None:
        return
    with transaction.atomic():
        assignment.revoked_at = timezone.now()
        assignment.save(update_fields=["revoked_at", "updated_at"])
        _reconcile_app_configs(
            device,
            list(device.apps.select_related("mcp_server_config")),
            None,
        )
        if assignment.agent_id:
            _sync_agent_skill(assignment.agent)
            _queue_agent_resume(assignment.agent_id)
    if revoked_by is not None and revoked_by.id != device.owner_id:
        record_computer_relay_event(
            "team_grant_revoked",
            user_id=revoked_by.id,
            analytics_event=AnalyticsEvent.COMPUTER_TEAM_GRANT_REVOKED,
            device_id=str(device.id),
            organization_id=str(assignment.organization_id) if assignment.organization_id else None,
        )


def revoke_device(device: ComputerDevice) -> None:
    with transaction.atomic():
        revoke_assignment(device)
        now = timezone.now()
        device.revoked_at = now
        device.credential_generation += 1
        device.save(update_fields=["revoked_at", "credential_generation", "updated_at"])
        device.credentials.filter(revoked_at__isnull=True).update(revoked_at=now)
        device.relay_artifacts.all().delete()
    send_device_control(device.id, "relay.close", {"code": "revoked"})
    record_computer_relay_event(
        "device_revoked",
        user_id=device.owner_id,
        analytics_event=AnalyticsEvent.COMPUTER_DEVICE_REVOKED,
        device_id=str(device.id),
    )


def _presence_key(device_id: uuid.UUID | str) -> str:
    return f"{_PRESENCE_KEY_PREFIX}{device_id}"


def _release_request_lock(redis_client, lock_key: str, lock_value: str) -> None:
    release = redis_client.register_script(
        """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        end
        return 0
        """
    )
    release(keys=[lock_key], args=[lock_value])


def _presence_value(*, channel_name: str, generation: str, ready: bool) -> str:
    return json.dumps(
        {"channel": channel_name, "generation": generation, "ready": ready},
        separators=(",", ":"),
    )


def _decode_presence(raw) -> dict[str, object] | None:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or not value.get("channel") or not value.get("generation"):
        return None
    return {
        "channel": str(value["channel"]),
        "generation": str(value["generation"]),
        "ready": bool(value.get("ready")),
    }


def claim_device_connection(device_id, *, channel_name: str, generation: str) -> dict[str, object] | None:
    redis_client = get_redis_client()
    claim = redis_client.register_script(
        """
        -- computer_presence_claim_v1
        local previous = redis.call("get", KEYS[1])
        redis.call("set", KEYS[1], ARGV[1], "EX", ARGV[2])
        return previous
        """
    )
    previous = claim(
        keys=[_presence_key(device_id)],
        args=[
            _presence_value(channel_name=channel_name, generation=generation, ready=False),
            settings.COMPUTER_CPP_PRESENCE_TTL_SECONDS,
        ],
    )
    return _decode_presence(previous)


def refresh_device_presence(device_id, *, channel_name: str, generation: str, ready: bool = True) -> bool:
    redis_client = get_redis_client()
    key = _presence_key(device_id)
    current_raw = redis_client.get(key)
    current = _decode_presence(current_raw)
    if current is None or current["generation"] != generation:
        return False
    refresh = redis_client.register_script(
        """
        -- computer_presence_cas_v1
        if redis.call("get", KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call("set", KEYS[1], ARGV[2], "EX", ARGV[3])
        return 1
        """
    )
    return bool(
        refresh(
            keys=[key],
            args=[
                current_raw,
                _presence_value(channel_name=channel_name, generation=generation, ready=ready),
                settings.COMPUTER_CPP_PRESENCE_TTL_SECONDS,
            ],
        )
    )


def set_device_presence(device_id, *, channel_name: str, generation: str) -> None:
    get_redis_client().set(
        _presence_key(device_id),
        _presence_value(channel_name=channel_name, generation=generation, ready=True),
        ex=settings.COMPUTER_CPP_PRESENCE_TTL_SECONDS,
    )


def get_device_presence(device_id) -> dict[str, str] | None:
    value = _decode_presence(get_redis_client().get(_presence_key(device_id)))
    if value is None or not value["ready"]:
        return None
    return {"channel": str(value["channel"]), "generation": str(value["generation"])}


def clear_device_presence(device_id, generation: str) -> None:
    redis_client = get_redis_client()
    key = _presence_key(device_id)
    current_raw = redis_client.get(key)
    current = _decode_presence(current_raw)
    if current and current["generation"] == generation:
        _release_request_lock(redis_client, key, current_raw)


def send_device_control(device_id, event_type: str, payload: dict[str, Any]) -> None:
    presence = get_device_presence(device_id)
    if not presence:
        return
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    from asgiref.sync import async_to_sync

    async_to_sync(channel_layer.send)(
        presence["channel"],
        {"type": "computer.control", "event_type": event_type, "payload": payload},
    )


async def relay_mcp_request(
    device_app_id: uuid.UUID | str,
    payload: dict[str, Any],
    *,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    app = await sync_to_async(
        lambda: ComputerDeviceApp.objects.select_related(
            "device__owner",
            "device__assignment",
            "mcp_server_config",
        )
        .filter(id=device_app_id)
        .first(),
        thread_sensitive=True,
    )()
    if app is None or not app.is_available or app.approval_state != ComputerDeviceApp.ApprovalState.APPROVED:
        raise ComputerRelayError("unknown_app", "This computer app is not approved")
    device = app.device
    assignment = getattr(device, "assignment", None)
    if (
        assignment is None
        or assignment.revoked_at is not None
        or assignment.agent_id is None
        or app.mcp_server_config is None
        or not app.mcp_server_config.is_active
    ):
        raise ComputerRelayError("disabled", "This computer app is not assigned to an agent")
    if device.revoked_at is not None:
        raise ComputerRelayError("offline", "This computer connection was revoked")
    if device.is_paused:
        raise ComputerRelayError("paused", "This computer connection is paused")
    if not await sync_to_async(computer_cpp_enabled_for_user, thread_sensitive=True)(device.owner):
        raise ComputerRelayError("disabled", "Computer connections are not enabled")

    presence = get_device_presence(device.id)
    if not presence:
        record_computer_relay_event("relay_call_failed", error_type="offline")
        raise ComputerRelayError("offline", f"{device.display_name} is offline", retryable=True)
    redis_client = get_redis_client()
    lock_key = f"{_REQUEST_LOCK_PREFIX}{device.id}"
    lock_value = secrets.token_urlsafe(16)
    timeout = timeout_seconds or settings.COMPUTER_CPP_REQUEST_TIMEOUT_SECONDS
    if not redis_client.set(lock_key, lock_value, nx=True, ex=timeout + 5):
        record_computer_relay_event("relay_call_failed", error_type="busy")
        raise ComputerRelayError("busy", f"{device.display_name} is busy", retryable=True)

    channel_layer = get_channel_layer()
    if channel_layer is None:
        _release_request_lock(redis_client, lock_key, lock_value)
        raise ComputerRelayError("internal_error", "Relay channel layer is unavailable", retryable=True)
    request_id = str(uuid.uuid4())
    reply_channel = await channel_layer.new_channel("computer.reply.")
    started_at = time.monotonic()
    outcome = "success"
    try:
        await channel_layer.send(
            presence["channel"],
            {
                "type": "computer.mcp_request",
                "request_id": request_id,
                "reply_channel": reply_channel,
                "connection_generation": presence["generation"],
                "app": app.app_key,
                "deadline_ms": timeout * 1000,
                "payload": payload,
            },
        )
        try:
            response = await asyncio.wait_for(channel_layer.receive(reply_channel), timeout=timeout)
        except TimeoutError as exc:
            outcome = "deadline_exceeded"
            await channel_layer.send(
                presence["channel"],
                {
                    "type": "computer.mcp_cancel",
                    "request_id": request_id,
                    "connection_generation": presence["generation"],
                },
            )
            raise ComputerRelayError(
                "deadline_exceeded",
                f"{device.display_name} did not respond before the deadline",
                retryable=True,
            ) from exc
        if response.get("request_id") != request_id:
            outcome = "correlation_failed"
            raise ComputerRelayError("internal_error", "Relay response correlation failed")
        if response.get("error"):
            error = response["error"] if isinstance(response["error"], dict) else {}
            outcome = str(error.get("code") or "internal_error")
            raise ComputerRelayError(
                str(error.get("code") or "internal_error"),
                str(error.get("message") or "The computer request failed"),
                retryable=bool(error.get("retryable")),
            )
        result = response.get("payload")
        if not isinstance(result, dict):
            outcome = "invalid_response"
            raise ComputerRelayError("internal_error", "Computer returned an invalid MCP response")
        return result
    finally:
        computer_relay_call_latency.record(
            time.monotonic() - started_at,
            {"outcome": outcome, "platform": device.platform},
        )
        record_computer_relay_event(
            "relay_call_completed" if outcome == "success" else "relay_call_failed",
            error_type="" if outcome == "success" else outcome,
            platform=device.platform,
        )
        _release_request_lock(redis_client, lock_key, lock_value)


def store_artifact(device: ComputerDevice, upload) -> ComputerRelayArtifact:
    mime_type = str(getattr(upload, "content_type", "") or "").lower()
    if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError("Only PNG, JPEG, and WebP artifacts are supported")
    size = int(getattr(upload, "size", 0) or 0)
    if size <= 0 or size > settings.COMPUTER_CPP_MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact size is invalid")
    body = upload.read()
    if len(body) != size or len(body) > settings.COMPUTER_CPP_MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact size is invalid")
    signatures_match = {
        "image/png": body.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": body.startswith(b"\xff\xd8\xff"),
        "image/webp": len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP",
    }
    if not signatures_match[mime_type]:
        raise ValueError("Artifact content does not match its declared image type")
    digest = hashlib.sha256(body).hexdigest()
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime_type]
    artifact_id = uuid.uuid4()
    storage_key = str(
        PurePosixPath("computer_relay")
        / str(device.id)
        / timezone.now().strftime("%Y/%m/%d")
        / f"{artifact_id}{suffix}"
    )
    saved_key = default_storage.save(storage_key, ContentFile(body))
    return ComputerRelayArtifact.objects.create(
        id=artifact_id,
        device=device,
        storage_key=saved_key,
        mime_type=mime_type,
        byte_count=size,
        sha256=digest,
        expires_at=timezone.now() + timedelta(seconds=settings.COMPUTER_CPP_ARTIFACT_TTL_SECONDS),
    )


def consume_artifact(device_id, artifact_id) -> dict[str, str]:
    with transaction.atomic():
        artifact = (
            ComputerRelayArtifact.objects.select_for_update()
            .filter(
                id=artifact_id,
                device_id=device_id,
                consumed_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        if artifact is None:
            raise ComputerRelayError("artifact_unavailable", "Computer artifact is unavailable")
        with default_storage.open(artifact.storage_key, "rb") as stored:
            body = stored.read(settings.COMPUTER_CPP_MAX_ARTIFACT_BYTES + 1)
        if len(body) != artifact.byte_count or hashlib.sha256(body).hexdigest() != artifact.sha256:
            raise ComputerRelayError("artifact_invalid", "Computer artifact failed integrity validation")
        import base64

        artifact.consumed_at = timezone.now()
        artifact.save(update_fields=["consumed_at"])
        transaction.on_commit(lambda: default_storage.delete(artifact.storage_key))
    return {
        "type": "image",
        "mimeType": artifact.mime_type,
        "data": base64.b64encode(body).decode("ascii"),
    }


def serialize_device(device: ComputerDevice, *, owner_actions: bool) -> dict[str, Any]:
    assignment = getattr(device, "assignment", None)
    if assignment and assignment.revoked_at is not None:
        assignment = None
    presence = get_device_presence(device.id)
    return {
        "id": str(device.id),
        "display_name": device.display_name,
        "platform": device.platform,
        "architecture": device.architecture,
        "client_version": device.client_version,
        "paused": device.is_paused,
        "online": bool(presence),
        "update_required": not computer_client_version_supported(device.client_version),
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "owner": {
            "display_name": device.owner.get_full_name()
            or device.owner.get_username()
            or "Computer owner",
        },
        "assignment": (
            {
                "agent_id": str(assignment.agent_id),
                "agent_name": assignment.agent.name,
                "organization_name": assignment.organization.name if assignment.organization else None,
            }
            if assignment
            else None
        ),
        "apps": [
            {
                "app_key": app.app_key,
                "display_name": app.display_name,
                "schema_sha256": app.reported_schema_hash,
                "approval_state": app.approval_state,
                "available": app.is_available,
            }
            for app in sorted(device.apps.all(), key=lambda value: value.display_name.lower())
        ],
        "permissions": {
            "can_manage_device": owner_actions,
        },
    }
