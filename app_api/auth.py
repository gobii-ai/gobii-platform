import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework import authentication, exceptions

from api.models import NativeAppSession


def _hash_token_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _token_deadline(*, ttl_seconds: int):
    return timezone.now() + timedelta(seconds=ttl_seconds)


def _split_raw_token(raw_token: str) -> tuple[uuid.UUID, str] | None:
    session_id, separator, secret = (raw_token or "").strip().partition(".")
    if not separator or not session_id or not secret:
        return None
    try:
        return uuid.UUID(session_id), secret
    except (TypeError, ValueError):
        return None


def _issue_raw_token(session_id: uuid.UUID) -> tuple[str, str]:
    secret = secrets.token_urlsafe(32)
    return f"{session_id}.{secret}", _hash_token_secret(secret)


def _get_request_user_agent(request) -> str:
    if request is None:
        return ""
    return (request.META.get("HTTP_USER_AGENT", "") or "")[:512]


def _get_request_metadata(request, *, body: dict | None = None) -> dict[str, str]:
    payload = body or {}
    return {
        "device_name": str(payload.get("device_name") or payload.get("deviceName") or "").strip()[:128],
        "device_platform": str(
            payload.get("device_platform") or payload.get("devicePlatform") or ""
        ).strip()[:64],
        "app_version": str(payload.get("app_version") or payload.get("appVersion") or "").strip()[:64],
        "user_agent": _get_request_user_agent(request),
    }


@dataclass(frozen=True)
class NativeAppSessionCredentials:
    session: NativeAppSession
    access_token: str
    refresh_token: str

    @property
    def access_expires_at(self):
        return self.session.access_expires_at

    @property
    def refresh_expires_at(self):
        return self.session.refresh_expires_at


def create_native_app_session(user, *, request=None, body: dict | None = None) -> NativeAppSessionCredentials:
    metadata = _get_request_metadata(request, body=body)
    session = NativeAppSession.objects.create(
        user=user,
        access_token_hash="",
        refresh_token_hash="",
        access_expires_at=_token_deadline(ttl_seconds=settings.NATIVE_APP_ACCESS_TOKEN_TTL_SECONDS),
        refresh_expires_at=_token_deadline(ttl_seconds=settings.NATIVE_APP_REFRESH_TOKEN_TTL_SECONDS),
        last_used_at=timezone.now(),
        device_name=metadata["device_name"],
        device_platform=metadata["device_platform"],
        app_version=metadata["app_version"],
        user_agent=metadata["user_agent"],
    )
    access_token, access_hash = _issue_raw_token(session.id)
    refresh_token, refresh_hash = _issue_raw_token(session.id)
    session.access_token_hash = access_hash
    session.refresh_token_hash = refresh_hash
    session.save(update_fields=["access_token_hash", "refresh_token_hash"])
    return NativeAppSessionCredentials(
        session=session,
        access_token=access_token,
        refresh_token=refresh_token,
    )


def rotate_native_app_session(
    session: NativeAppSession,
    *,
    request=None,
    body: dict | None = None,
) -> NativeAppSessionCredentials:
    metadata = _get_request_metadata(request, body=body)
    access_token, access_hash = _issue_raw_token(session.id)
    refresh_token, refresh_hash = _issue_raw_token(session.id)
    session.access_token_hash = access_hash
    session.refresh_token_hash = refresh_hash
    session.access_expires_at = _token_deadline(ttl_seconds=settings.NATIVE_APP_ACCESS_TOKEN_TTL_SECONDS)
    session.refresh_expires_at = _token_deadline(ttl_seconds=settings.NATIVE_APP_REFRESH_TOKEN_TTL_SECONDS)
    session.last_used_at = timezone.now()
    if metadata["device_name"]:
        session.device_name = metadata["device_name"]
    if metadata["device_platform"]:
        session.device_platform = metadata["device_platform"]
    if metadata["app_version"]:
        session.app_version = metadata["app_version"]
    if metadata["user_agent"]:
        session.user_agent = metadata["user_agent"]
    session.revoked_at = None
    session.save(
        update_fields=[
            "access_token_hash",
            "refresh_token_hash",
            "access_expires_at",
            "refresh_expires_at",
            "last_used_at",
            "device_name",
            "device_platform",
            "app_version",
            "user_agent",
            "revoked_at",
            "updated_at",
        ]
    )
    return NativeAppSessionCredentials(
        session=session,
        access_token=access_token,
        refresh_token=refresh_token,
    )


def revoke_native_app_session(session: NativeAppSession) -> NativeAppSession:
    if session.revoked_at is not None:
        return session
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at", "updated_at"])
    return session


def _authenticate_session_token(
    raw_token: str,
    *,
    token_kind: str,
    update_last_used: bool,
) -> NativeAppSession | None:
    parsed = _split_raw_token(raw_token)
    if not parsed:
        return None
    session_id, secret = parsed
    session = (
        NativeAppSession.objects.select_related("user")
        .filter(id=session_id)
        .first()
    )
    if session is None or session.revoked_at is not None:
        return None
    expires_at = session.access_expires_at if token_kind == "access" else session.refresh_expires_at
    if timezone.now() >= expires_at:
        return None
    expected_hash = session.access_token_hash if token_kind == "access" else session.refresh_token_hash
    if not hmac.compare_digest(expected_hash, _hash_token_secret(secret)):
        return None
    if not session.user or not session.user.is_active:
        return None
    if update_last_used:
        session.last_used_at = timezone.now()
        session.save(update_fields=["last_used_at", "updated_at"])
    return session


def authenticate_native_app_access_token(raw_token: str) -> NativeAppSession | None:
    return _authenticate_session_token(
        raw_token,
        token_kind="access",
        update_last_used=True,
    )


def authenticate_native_app_refresh_token(raw_token: str) -> NativeAppSession | None:
    return _authenticate_session_token(
        raw_token,
        token_kind="refresh",
        update_last_used=False,
    )


def get_bearer_token_from_headers(headers) -> str | None:
    raw_header = headers.get("Authorization") or headers.get("authorization") or ""
    scheme, _, token = raw_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


class NativeAppAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        raw_token = get_bearer_token_from_headers(request.headers)
        if not raw_token:
            return None
        session = authenticate_native_app_access_token(raw_token)
        if session is None:
            raise exceptions.AuthenticationFailed("Invalid or expired app token.")
        return (session.user, session)


class AppTokenAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        scope["session"] = None
        headers = {
            key.decode("latin1"): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        token = get_bearer_token_from_headers(headers)
        if token:
            session = await database_sync_to_async(authenticate_native_app_access_token)(token)
            if session is not None:
                scope["user"] = session.user
                scope["auth"] = session
            else:
                scope["user"] = AnonymousUser()
                scope["auth"] = None
        else:
            scope["user"] = AnonymousUser()
            scope["auth"] = None
        return await self.app(scope, receive, send)
