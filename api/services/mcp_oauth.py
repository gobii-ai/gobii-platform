import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import redis
import requests
from django.db import DatabaseError
from django.utils import timezone
from pottery import Redlock
from pottery.exceptions import PotteryError

from config.redis_client import get_redis_client

from api.models import MCPServerConfig, MCPServerOAuthCredential

logger = logging.getLogger(__name__)

OAUTH_REFRESH_SAFETY_MARGIN = timedelta(minutes=2)
OAUTH_REFRESH_TIMEOUT_SECONDS = 15
OAUTH_REFRESH_LOCK_TTL_SECONDS = 30
OAUTH_TRANSIENT_FAILURE_TTL_SECONDS = 5 * 60
OAUTH_RECONNECT_FAILURE_TTL_SECONDS = 7 * 24 * 60 * 60
OAUTH_CACHE_PREFIX = "mcp:oauth:v1"


class MCPOAuthStatus:
    USABLE = "usable"
    RECONNECT_REQUIRED = "reconnect_required"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    CONFIGURATION_ERROR = "configuration_error"


@dataclass(frozen=True)
class MCPOAuthResult:
    status: str
    credential: Optional[MCPServerOAuthCredential]

    @property
    def message(self) -> str:
        return _STATUS_MESSAGES.get(self.status, "")


_STATUS_MESSAGES = {
    MCPOAuthStatus.RECONNECT_REQUIRED: "This MCP integration must be connected or reconnected before it can be used.",
    MCPOAuthStatus.TEMPORARILY_UNAVAILABLE: "This MCP integration is temporarily unavailable.",
    MCPOAuthStatus.CONFIGURATION_ERROR: "This MCP integration's OAuth configuration is incomplete.",
}


def _cache_key(kind: str, credential: MCPServerOAuthCredential) -> str:
    revision = credential.updated_at.isoformat() if credential.updated_at else "unknown"
    return f"{OAUTH_CACHE_PREFIX}:{kind}:{credential.id}:{revision}"


def _read_failure(credential: MCPServerOAuthCredential) -> Optional[str]:
    try:
        payload = get_redis_client().get(_cache_key("failure", credential))
        return payload.decode("utf-8") if isinstance(payload, bytes) else payload
    except (UnicodeDecodeError, redis.exceptions.RedisError):
        logger.debug("Failed to read MCP OAuth failure state for %s", credential.id, exc_info=True)
        return None


def _write_failure(credential: MCPServerOAuthCredential, status: str) -> None:
    ttl = (
        OAUTH_RECONNECT_FAILURE_TTL_SECONDS
        if status == MCPOAuthStatus.RECONNECT_REQUIRED
        else OAUTH_TRANSIENT_FAILURE_TTL_SECONDS
    )
    try:
        get_redis_client().set(_cache_key("failure", credential), status, ex=ttl)
    except redis.exceptions.RedisError:
        logger.debug("Failed to cache MCP OAuth failure for %s", credential.id, exc_info=True)


def _load_config(config_id: str) -> Optional[MCPServerConfig]:
    return MCPServerConfig.objects.filter(id=config_id, is_active=True).select_related("oauth_credential").first()


def _load_config_result(
    config_id: str,
) -> tuple[Optional[MCPServerConfig], Optional[MCPOAuthResult]]:
    try:
        return _load_config(config_id), None
    except DatabaseError:
        logger.exception("Failed to load MCP OAuth configuration %s", config_id)
        return None, MCPOAuthResult(MCPOAuthStatus.TEMPORARILY_UNAVAILABLE, None)


def _credential_for_config(config: MCPServerConfig) -> Optional[MCPServerOAuthCredential]:
    try:
        return config.oauth_credential
    except MCPServerOAuthCredential.DoesNotExist:
        return None


def _token_is_valid(credential: MCPServerOAuthCredential, *, now=None) -> bool:
    return bool((credential.access_token or "").strip()) and (
        credential.expires_at is None or credential.expires_at > (now or timezone.now())
    )


def _needs_refresh(credential: MCPServerOAuthCredential, *, now=None) -> bool:
    now = now or timezone.now()
    return not (credential.access_token or "").strip() or bool(
        credential.expires_at
        and credential.expires_at <= now + OAUTH_REFRESH_SAFETY_MARGIN
    )


def _cached_failure_result(credential: MCPServerOAuthCredential) -> Optional[MCPOAuthResult]:
    status = _read_failure(credential)
    if status not in _STATUS_MESSAGES:
        return None
    if _token_is_valid(credential):
        return MCPOAuthResult(MCPOAuthStatus.USABLE, credential)
    return MCPOAuthResult(status, credential)


def _oauth_error_status(response: requests.Response, payload: object) -> str:
    error_code = payload.get("error") if isinstance(payload, dict) else None
    if error_code == "invalid_grant":
        return MCPOAuthStatus.RECONNECT_REQUIRED
    if error_code == "invalid_client" or response.status_code in {401, 403}:
        return MCPOAuthStatus.CONFIGURATION_ERROR
    return MCPOAuthStatus.TEMPORARILY_UNAVAILABLE


def _refresh_credential(
    config: MCPServerConfig,
    credential: MCPServerOAuthCredential,
) -> MCPOAuthResult:
    now = timezone.now()
    refresh_token = (credential.refresh_token or "").strip()
    if not refresh_token:
        status = MCPOAuthStatus.USABLE if _token_is_valid(credential, now=now) else MCPOAuthStatus.RECONNECT_REQUIRED
        return MCPOAuthResult(status, credential)

    credential_metadata = credential.metadata if isinstance(credential.metadata, dict) else {}
    config_metadata = config.metadata if isinstance(config.metadata, dict) else {}
    token_endpoint = str(
        credential_metadata.get("token_endpoint")
        or config_metadata.get("token_endpoint")
        or ""
    ).strip()
    if not token_endpoint:
        return MCPOAuthResult(MCPOAuthStatus.CONFIGURATION_ERROR, credential)

    request_data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    for field in ("client_id", "client_secret"):
        value = str(getattr(credential, field) or config_metadata.get(field) or "").strip()
        if value:
            request_data[field] = value

    try:
        response = requests.post(
            token_endpoint,
            data=request_data,
            timeout=OAUTH_REFRESH_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return MCPOAuthResult(MCPOAuthStatus.TEMPORARILY_UNAVAILABLE, credential)

    try:
        payload = response.json()
    except ValueError:
        payload = None
    try:
        response.raise_for_status()
    except requests.HTTPError:
        return MCPOAuthResult(_oauth_error_status(response, payload), credential)
    if not isinstance(payload, dict) or not str(payload.get("access_token") or "").strip():
        return MCPOAuthResult(MCPOAuthStatus.TEMPORARILY_UNAVAILABLE, credential)

    access_token = str(payload["access_token"]).strip()
    updates = {"access_token_encrypted": credential._encrypt_text(access_token)}
    for payload_field, encrypted_field in (
        ("refresh_token", "refresh_token_encrypted"),
        ("id_token", "id_token_encrypted"),
    ):
        value = str(payload.get(payload_field) or "").strip()
        if value:
            updates[encrypted_field] = credential._encrypt_text(value)
    for field in ("token_type", "scope"):
        value = str(payload.get(field) or "").strip()
        if value:
            updates[field] = value

    try:
        expires_in = payload.get("expires_in")
        expires_at = (
            None
            if expires_in is None
            else now + timedelta(seconds=max(int(expires_in), 0))
        )
    except (TypeError, ValueError):
        expires_at = None
    updates["expires_at"] = expires_at

    metadata = dict(credential_metadata)
    metadata["last_refresh_response"] = {
        key: value
        for key, value in payload.items()
        if key not in {"access_token", "refresh_token", "id_token"}
    }
    updates.update(metadata=metadata, updated_at=now)
    try:
        # Values are encrypted explicitly; update() avoids reconnect-driven catalog invalidation.
        updated = MCPServerOAuthCredential.objects.filter(id=credential.id).update(**updates)
    except DatabaseError:
        logger.exception("Failed to persist refreshed MCP OAuth credential %s", credential.id)
        return MCPOAuthResult(MCPOAuthStatus.TEMPORARILY_UNAVAILABLE, credential)
    if not updated:
        return MCPOAuthResult(MCPOAuthStatus.RECONNECT_REQUIRED, credential)

    credential.access_token_encrypted = updates["access_token_encrypted"]
    for field in ("refresh_token_encrypted", "id_token_encrypted", "token_type", "scope"):
        if field in updates:
            setattr(credential, field, updates[field])
    credential.expires_at = expires_at
    credential.metadata = metadata
    credential.updated_at = now
    return MCPOAuthResult(MCPOAuthStatus.USABLE, credential)


def _reload_after_lock(config_id: str, *, may_refresh: bool) -> MCPOAuthResult:
    config, load_error = _load_config_result(config_id)
    if load_error is not None:
        return load_error
    credential = _credential_for_config(config) if config else None
    if config is None or credential is None:
        return MCPOAuthResult(MCPOAuthStatus.RECONNECT_REQUIRED, None)
    if not _needs_refresh(credential):
        return MCPOAuthResult(MCPOAuthStatus.USABLE, credential)
    if not may_refresh:
        return _cached_failure_result(credential) or MCPOAuthResult(
            MCPOAuthStatus.TEMPORARILY_UNAVAILABLE, credential
        )

    had_valid_token = _token_is_valid(credential)
    result = _refresh_credential(config, credential)
    if result.status == MCPOAuthStatus.USABLE:
        return result
    _write_failure(credential, result.status)
    if had_valid_token:
        return MCPOAuthResult(MCPOAuthStatus.USABLE, credential)
    return result


def ensure_mcp_oauth_credential(config_id: str) -> MCPOAuthResult:
    """Return a usable credential, coordinating refreshes for this MCP server."""
    config, load_error = _load_config_result(config_id)
    if load_error is not None:
        return load_error
    if config is None:
        return MCPOAuthResult(MCPOAuthStatus.CONFIGURATION_ERROR, None)

    credential = _credential_for_config(config)
    if config.auth_method != MCPServerConfig.AuthMethod.OAUTH2:
        return MCPOAuthResult(MCPOAuthStatus.USABLE, credential)
    if credential is None:
        return MCPOAuthResult(MCPOAuthStatus.RECONNECT_REQUIRED, None)
    if not _needs_refresh(credential):
        return MCPOAuthResult(MCPOAuthStatus.USABLE, credential)

    cached_failure = _cached_failure_result(credential)
    if cached_failure is not None:
        return cached_failure

    lock = None
    acquired = True
    try:
        lock = Redlock(
            key=_cache_key("refresh", credential),
            masters={get_redis_client()},
            raise_on_redis_errors=True,
            auto_release_time=OAUTH_REFRESH_LOCK_TTL_SECONDS,
        )
        acquired = lock.acquire(timeout=OAUTH_REFRESH_TIMEOUT_SECONDS + 1)
    except (redis.exceptions.RedisError, PotteryError):
        logger.debug("MCP OAuth locking unavailable for %s", config_id, exc_info=True)
        lock = None

    try:
        result = _reload_after_lock(config_id, may_refresh=acquired)
    finally:
        if lock is not None and acquired:
            try:
                lock.release()
            except (redis.exceptions.RedisError, PotteryError):
                logger.debug("Failed to release MCP OAuth lock for %s", config_id, exc_info=True)

    return result
