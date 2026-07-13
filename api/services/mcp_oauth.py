import logging
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
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
    refreshed: bool = False
    cache_state: str = "none"
    message: str = ""


def _credential_revision(credential: MCPServerOAuthCredential) -> str:
    updated_at = credential.updated_at
    return updated_at.isoformat() if updated_at else "unknown"


def _refresh_lock_key(credential: MCPServerOAuthCredential) -> str:
    return f"{OAUTH_CACHE_PREFIX}:refresh:{credential.id}:{_credential_revision(credential)}"


def _failure_key(credential: MCPServerOAuthCredential) -> str:
    return f"{OAUTH_CACHE_PREFIX}:failure:{credential.id}:{_credential_revision(credential)}"


def _read_failure(credential: MCPServerOAuthCredential) -> Optional[str]:
    try:
        payload = get_redis_client().get(_failure_key(credential))
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
    except (UnicodeDecodeError, redis.exceptions.RedisError):
        logger.debug("Failed to read MCP OAuth failure state for %s", credential.id, exc_info=True)
        return None
    return payload if isinstance(payload, str) else None


def _write_failure(credential: MCPServerOAuthCredential, status: str) -> None:
    ttl = (
        OAUTH_RECONNECT_FAILURE_TTL_SECONDS
        if status == MCPOAuthStatus.RECONNECT_REQUIRED
        else OAUTH_TRANSIENT_FAILURE_TTL_SECONDS
    )
    try:
        get_redis_client().set(
            _failure_key(credential),
            status,
            ex=ttl,
        )
    except redis.exceptions.RedisError:
        logger.debug("Failed to cache MCP OAuth failure for %s", credential.id, exc_info=True)


def _load_config(config_id: str) -> Optional[MCPServerConfig]:
    try:
        return (
            MCPServerConfig.objects.filter(id=config_id, is_active=True)
            .select_related("oauth_credential")
            .first()
        )
    except DatabaseError:
        logger.exception("Failed to load MCP OAuth configuration %s", config_id)
        return None


def _credential_for_config(config: MCPServerConfig) -> Optional[MCPServerOAuthCredential]:
    try:
        return config.oauth_credential
    except MCPServerOAuthCredential.DoesNotExist:
        return None


def _token_is_valid(credential: MCPServerOAuthCredential, *, now=None) -> bool:
    if not (credential.access_token or "").strip():
        return False
    expires_at = credential.expires_at
    return expires_at is None or expires_at > (now or timezone.now())


def _needs_refresh(credential: MCPServerOAuthCredential, *, now=None) -> bool:
    now = now or timezone.now()
    if not (credential.access_token or "").strip():
        return True
    expires_at = credential.expires_at
    return bool(expires_at and expires_at <= now + OAUTH_REFRESH_SAFETY_MARGIN)


def _failure_result(
    credential: MCPServerOAuthCredential,
    status: str,
    *,
    cache_state: str,
) -> MCPOAuthResult:
    if status == MCPOAuthStatus.RECONNECT_REQUIRED:
        message = "This MCP integration must be reconnected before it can be used."
    elif status == MCPOAuthStatus.CONFIGURATION_ERROR:
        message = "This MCP integration's OAuth configuration is incomplete."
    else:
        message = "This MCP integration is temporarily unavailable while authentication is refreshed."
    return MCPOAuthResult(
        status=status,
        credential=credential,
        cache_state=cache_state,
        message=message,
    )


def _cached_failure_result(credential: MCPServerOAuthCredential) -> Optional[MCPOAuthResult]:
    status = _read_failure(credential)
    if status not in {
        MCPOAuthStatus.RECONNECT_REQUIRED,
        MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
        MCPOAuthStatus.CONFIGURATION_ERROR,
    }:
        return None
    if _token_is_valid(credential):
        return MCPOAuthResult(
            status=MCPOAuthStatus.USABLE,
            credential=credential,
            cache_state="failure_bypassed_with_valid_token",
        )
    return _failure_result(credential, status, cache_state="failure_hit")


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
        if _token_is_valid(credential, now=now):
            return MCPOAuthResult(status=MCPOAuthStatus.USABLE, credential=credential)
        return _failure_result(
            credential,
            MCPOAuthStatus.RECONNECT_REQUIRED,
            cache_state="missing_refresh_token",
        )

    credential_metadata = credential.metadata if isinstance(credential.metadata, dict) else {}
    config_metadata = config.metadata if isinstance(config.metadata, dict) else {}
    token_endpoint = (
        credential_metadata.get("token_endpoint")
        or config_metadata.get("token_endpoint")
        or ""
    ).strip()
    if not token_endpoint:
        return _failure_result(
            credential,
            MCPOAuthStatus.CONFIGURATION_ERROR,
            cache_state="missing_token_endpoint",
        )

    request_data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    client_id = (credential.client_id or config_metadata.get("client_id") or "").strip()
    if client_id:
        request_data["client_id"] = client_id
    client_secret = (credential.client_secret or config_metadata.get("client_secret") or "").strip()
    if client_secret:
        request_data["client_secret"] = client_secret

    try:
        response = requests.post(
            token_endpoint,
            data=request_data,
            timeout=OAUTH_REFRESH_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return _failure_result(
            credential,
            MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
            cache_state="request_failed",
        )

    try:
        token_payload = response.json()
    except ValueError:
        token_payload = None

    try:
        response.raise_for_status()
    except requests.HTTPError:
        return _failure_result(
            credential,
            _oauth_error_status(response, token_payload),
            cache_state="response_failed",
        )

    if not isinstance(token_payload, dict):
        return _failure_result(
            credential,
            MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
            cache_state="invalid_response",
        )
    new_access_token = str(token_payload.get("access_token") or "").strip()
    if not new_access_token:
        return _failure_result(
            credential,
            MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
            cache_state="missing_access_token",
        )

    credential.access_token = new_access_token
    updates = {"access_token_encrypted": credential.access_token_encrypted}
    new_refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if new_refresh_token:
        credential.refresh_token = new_refresh_token
        updates["refresh_token_encrypted"] = credential.refresh_token_encrypted
    new_id_token = str(token_payload.get("id_token") or "").strip()
    if new_id_token:
        credential.id_token = new_id_token
        updates["id_token_encrypted"] = credential.id_token_encrypted
    token_type = str(token_payload.get("token_type") or "").strip()
    if token_type:
        credential.token_type = token_type
        updates["token_type"] = token_type
    scope = str(token_payload.get("scope") or "").strip()
    if scope:
        credential.scope = scope
        updates["scope"] = scope

    expires_in = token_payload.get("expires_in")
    if expires_in is None:
        credential.expires_at = None
    else:
        try:
            credential.expires_at = now + timedelta(seconds=max(int(expires_in), 0))
        except (TypeError, ValueError):
            credential.expires_at = None
    updates["expires_at"] = credential.expires_at

    metadata = dict(credential_metadata)
    metadata["last_refresh_response"] = {
        key: value
        for key, value in token_payload.items()
        if key not in {"access_token", "refresh_token", "id_token"}
    }
    credential.metadata = metadata
    credential.updated_at = now
    updates.update(metadata=metadata, updated_at=now)
    try:
        # Token rotation does not change tool schemas, so bypass the model signal
        # that invalidates and rediscovers the catalog for user-driven reconnects.
        updated = MCPServerOAuthCredential.objects.filter(id=credential.id).update(**updates)
    except DatabaseError:
        logger.exception("Failed to persist refreshed MCP OAuth credential %s", credential.id)
        return _failure_result(
            credential,
            MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
            cache_state="persistence_failed",
        )
    if not updated:
        return _failure_result(
            credential,
            MCPOAuthStatus.RECONNECT_REQUIRED,
            cache_state="credential_removed",
        )
    return MCPOAuthResult(
        status=MCPOAuthStatus.USABLE,
        credential=credential,
        refreshed=True,
        cache_state="refreshed",
    )


def ensure_mcp_oauth_credential(config_id: str) -> MCPOAuthResult:
    """Return a usable credential, refreshing only this MCP server when required."""
    started_at = monotonic()
    config = _load_config(config_id)
    if config is None:
        result = MCPOAuthResult(
            status=MCPOAuthStatus.CONFIGURATION_ERROR,
            credential=None,
            message="This MCP integration is unavailable.",
        )
        logger.info(
            "MCP OAuth preparation: config=%s status=%s cache=%s duration_ms=%d",
            config_id,
            result.status,
            result.cache_state,
            round((monotonic() - started_at) * 1000),
        )
        return result

    credential = _credential_for_config(config)
    if config.auth_method != MCPServerConfig.AuthMethod.OAUTH2:
        return MCPOAuthResult(status=MCPOAuthStatus.USABLE, credential=credential)
    if credential is None:
        return MCPOAuthResult(
            status=MCPOAuthStatus.RECONNECT_REQUIRED,
            credential=None,
            message="This MCP integration must be connected before it can be used.",
        )
    if not _needs_refresh(credential):
        return MCPOAuthResult(status=MCPOAuthStatus.USABLE, credential=credential)

    cached_failure = _cached_failure_result(credential)
    if cached_failure is not None:
        return cached_failure

    lock = None
    acquired = True
    lock_state = "local"
    try:
        lock = Redlock(
            key=_refresh_lock_key(credential),
            masters={get_redis_client()},
            raise_on_redis_errors=True,
            auto_release_time=OAUTH_REFRESH_LOCK_TTL_SECONDS,
        )
        acquired = lock.acquire(timeout=OAUTH_REFRESH_TIMEOUT_SECONDS + 1)
        lock_state = "acquired" if acquired else "timeout"
    except (redis.exceptions.RedisError, PotteryError):
        logger.debug("MCP OAuth locking unavailable for %s", config_id, exc_info=True)
        acquired = True
        lock = None
        lock_state = "unavailable"

    if not acquired:
        reloaded = _load_config(config_id)
        reloaded_credential = _credential_for_config(reloaded) if reloaded else None
        if reloaded_credential and not _needs_refresh(reloaded_credential):
            result = MCPOAuthResult(
                status=MCPOAuthStatus.USABLE,
                credential=reloaded_credential,
                refreshed=True,
                cache_state="waited_for_refresh",
            )
        elif reloaded_credential:
            result = _cached_failure_result(reloaded_credential) or _failure_result(
                reloaded_credential,
                MCPOAuthStatus.TEMPORARILY_UNAVAILABLE,
                cache_state="lock_timeout",
            )
        else:
            result = MCPOAuthResult(
                status=MCPOAuthStatus.RECONNECT_REQUIRED,
                credential=None,
                cache_state="lock_timeout",
                message="This MCP integration must be reconnected before it can be used.",
            )
    else:
        try:
            reloaded = _load_config(config_id)
            reloaded_credential = _credential_for_config(reloaded) if reloaded else None
            if reloaded is None or reloaded_credential is None:
                result = MCPOAuthResult(
                    status=MCPOAuthStatus.RECONNECT_REQUIRED,
                    credential=None,
                    message="This MCP integration must be reconnected before it can be used.",
                )
            elif not _needs_refresh(reloaded_credential):
                result = MCPOAuthResult(
                    status=MCPOAuthStatus.USABLE,
                    credential=reloaded_credential,
                    cache_state="refreshed_by_peer",
                )
            else:
                result = _refresh_credential(reloaded, reloaded_credential)
                if result.status != MCPOAuthStatus.USABLE:
                    _write_failure(reloaded_credential, result.status)
                    if _token_is_valid(reloaded_credential):
                        result = MCPOAuthResult(
                            status=MCPOAuthStatus.USABLE,
                            credential=reloaded_credential,
                            cache_state="refresh_failed_with_valid_token",
                        )
        finally:
            if lock is not None:
                try:
                    lock.release()
                except (redis.exceptions.RedisError, PotteryError):
                    logger.debug("Failed to release MCP OAuth lock for %s", config_id, exc_info=True)

    logger.info(
        "MCP OAuth preparation: config=%s status=%s cache=%s lock=%s refreshed=%s duration_ms=%d",
        config_id,
        result.status,
        result.cache_state,
        lock_state,
        result.refreshed,
        round((monotonic() - started_at) * 1000),
    )
    return result
