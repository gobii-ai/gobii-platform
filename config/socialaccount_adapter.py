"""Custom django-allauth social account adapter hooks."""

import logging
import secrets

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core import signing
from django.http import HttpResponseRedirect, HttpRequest
from django.urls import reverse

from agents.services import PretrainedWorkerTemplateService
from api.services.system_settings import get_account_allow_social_signup
from config.redis_client import get_redis_client
from util.onboarding import (
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
)


logger = logging.getLogger(__name__)

# Session keys to preserve during social auth flow
OAUTH_CHARTER_SESSION_KEYS = (
    "agent_charter",
    "agent_charter_override",
    PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY,
    "agent_charter_source",
    "agent_preferred_llm_tier",
    "agent_selected_pipedream_app_slugs",
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
)

OAUTH_ATTRIBUTION_SESSION_KEYS = (
    "utm_first_touch",
    "utm_last_touch",
    "click_ids_first",
    "click_ids_last",
    "fbclid_first",
    "fbclid_last",
    "utm_querystring",
)

# Cookie name for stashing charter data during OAuth
OAUTH_CHARTER_COOKIE = "gobii_oauth_charter"
OAUTH_ATTRIBUTION_COOKIE = "gobii_oauth_attribution"
OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY = "server_side_token"
OAUTH_CHARTER_STASH_CACHE_KEY_PREFIX = "oauth_charter_stash"
OAUTH_STASH_TTL_SECONDS = 7200


def build_oauth_charter_stash_cache_key(token: str) -> str:
    return f"{OAUTH_CHARTER_STASH_CACHE_KEY_PREFIX}:{token}"


def serialize_oauth_charter_cookie_payload(
    payload: dict[str, object],
    *,
    server_side: bool = False,
) -> str | None:
    if not server_side:
        return signing.dumps(payload, compress=True)

    token = secrets.token_urlsafe(24)
    try:
        redis_client = get_redis_client()
        redis_client.set(
            build_oauth_charter_stash_cache_key(token),
            signing.dumps(payload, compress=True),
            ex=OAUTH_STASH_TTL_SECONDS,
        )
    except Exception:
        logger.exception("Failed to persist server-side OAuth charter stash")
        return None
    return signing.dumps(
        {OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY: token},
        compress=True,
    )


def _resolve_oauth_charter_cookie_payload(stashed: object) -> dict[str, object] | None:
    if not isinstance(stashed, dict):
        return None

    token = stashed.get(OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY)
    if token is None:
        return stashed
    if not isinstance(token, str) or not token.strip():
        return None

    try:
        redis_client = get_redis_client()
        cached = redis_client.get(build_oauth_charter_stash_cache_key(token))
        if not isinstance(cached, str) or not cached:
            return None
        resolved = signing.loads(cached)
    except Exception:
        logger.exception("Failed loading server-side OAuth charter stash")
        return None

    if not isinstance(resolved, dict):
        return None
    return resolved


def _restore_session_keys_from_cookie(
    request: HttpRequest,
    *,
    cookie_name: str,
    keys: tuple[str, ...],
    overwrite_existing: bool = False,
) -> bool:
    cookie_value = request.COOKIES.get(cookie_name)
    if not cookie_value:
        return False

    try:
        stashed = signing.loads(cookie_value, max_age=OAUTH_STASH_TTL_SECONDS)
    except (signing.BadSignature, signing.SignatureExpired):
        logger.debug("Invalid or expired OAuth cookie: %s", cookie_name)
        return False

    if cookie_name == OAUTH_CHARTER_COOKIE:
        resolved_stashed = _resolve_oauth_charter_cookie_payload(stashed)
        if resolved_stashed is None:
            logger.debug("Invalid or expired server-side OAuth charter stash")
            return False
    elif isinstance(stashed, dict):
        resolved_stashed = stashed
    else:
        return False

    restored_any = False
    for key in keys:
        if key not in resolved_stashed:
            continue
        if not overwrite_existing and key in request.session:
            continue
        request.session[key] = resolved_stashed[key]
        restored_any = True

    if restored_any:
        request.session.modified = True

    return restored_any


def restore_oauth_session_state(
    request: HttpRequest,
    *,
    overwrite_existing: bool = False,
) -> bool:
    """Restore charter and attribution session keys from OAuth fallback cookies."""
    charter_restored = _restore_session_keys_from_cookie(
        request,
        cookie_name=OAUTH_CHARTER_COOKIE,
        keys=OAUTH_CHARTER_SESSION_KEYS,
        overwrite_existing=overwrite_existing,
    )
    attribution_restored = _restore_session_keys_from_cookie(
        request,
        cookie_name=OAUTH_ATTRIBUTION_COOKIE,
        keys=OAUTH_ATTRIBUTION_SESSION_KEYS,
        overwrite_existing=overwrite_existing,
    )
    return charter_restored or attribution_restored


class GobiiSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Tighten the social login flow for existing email/password users."""

    def is_open_for_signup(self, request: HttpRequest, sociallogin: SocialLogin) -> bool:
        return get_account_allow_social_signup()

    def pre_social_login(self, request: HttpRequest, social_login: SocialLogin) -> None:
        """Stop Google (or other) logins from hijacking password accounts.

        Also restore stashed OAuth session state (charter + attribution)
        when available.
        """
        if restore_oauth_session_state(request):
            logger.info("Restored OAuth session state during social login")

        # Allow normal processing when the social account already exists or the
        # user is connecting a provider while authenticated.
        if request.user.is_authenticated or social_login.account.pk:
            return

        email = (getattr(social_login.user, "email", None) or "").strip()
        if not email:
            return

        UserModel = get_user_model()
        try:
            existing_user = UserModel.objects.get(email__iexact=email)
        except UserModel.DoesNotExist:
            return

        provider_id = social_login.account.provider

        logger.info(
            "Social login blocked because email already exists",
            extra={
                "provider": provider_id,
                "email": email,
                "existing_user_id": existing_user.pk,
            },
        )

        messages.error(
            request,
            f"We already have an account for {email}. Please sign in with your email and password.",
        )

        raise ImmediateHttpResponse(HttpResponseRedirect(reverse("account_login")))
