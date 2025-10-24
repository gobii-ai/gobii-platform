"""Celery tasks for sending marketing conversion events."""

import json
import logging
from typing import Any, Dict, Optional

import requests
from celery import shared_task
from requests import Response

from django.conf import settings

from .conversions import build_conversion_event, build_facebook_payload, build_reddit_payload


logger = logging.getLogger(__name__)


FACEBOOK_ENDPOINT_TEMPLATE = "https://graph.facebook.com/v17.0/{pixel_id}/events"
REDDIT_CONVERSIONS_ENDPOINT = getattr(
    settings,
    "REDDIT_CONVERSIONS_ENDPOINT",
    "https://ads-api.reddit.com/api/v2.0/conversions/events",
)
REDDIT_TOKEN_ENDPOINT = getattr(
    settings,
    "REDDIT_TOKEN_ENDPOINT",
    "https://www.reddit.com/api/v1/access_token",
)


def _retry_delay(attempt: int) -> int:
    """Return an exponential backoff delay with an upper bound."""

    return min(60 * (2 ** max(attempt - 1, 0)), 600)


def _should_retry(status_code: int) -> bool:
    """Return True if a status code indicates a transient error."""

    return status_code >= 500 or status_code in {408, 429}


def _log_http_error(response: Response, provider: str) -> None:
    try:
        content = response.json()
    except Exception:
        content = response.text

    logger.warning(
        "%s conversion API error (status=%s, body=%s)",
        provider,
        response.status_code,
        content,
    )


def _post_json(url: str, *, params: Optional[Dict[str, Any]] = None, json_payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Response:
    """Issue a JSON POST request with a sensible timeout."""

    return requests.post(
        url,
        params=params,
        json=json_payload,
        headers=headers,
        timeout=10,
    )


def _get_reddit_access_token() -> Optional[str]:
    """Return an access token for Reddit's Conversions API."""

    if getattr(settings, "REDDIT_ACCESS_TOKEN", ""):
        return settings.REDDIT_ACCESS_TOKEN

    client_id = getattr(settings, "REDDIT_CLIENT_ID", "")
    client_secret = getattr(settings, "REDDIT_CLIENT_SECRET", "")
    refresh_token = getattr(settings, "REDDIT_REFRESH_TOKEN", "")

    if not (client_id and client_secret and refresh_token):
        logger.debug("Reddit credentials missing; skipping token refresh.")
        return None

    try:
        response = requests.post(
            REDDIT_TOKEN_ENDPOINT,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(client_id, client_secret),
            headers={
                "User-Agent": getattr(settings, "REDDIT_USER_AGENT", "gobii-platform/1.0"),
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Reddit token request failed: %s", exc)
        return None

    if response.status_code != 200:
        _log_http_error(response, "Reddit token")
        return None

    try:
        payload = response.json()
    except json.JSONDecodeError:
        logger.warning("Reddit token response was not JSON: %s", response.text)
        return None

    token = payload.get("access_token")
    if not token:
        logger.warning("Reddit token response missing access_token field: %s", payload)

    return token


@shared_task(bind=True, max_retries=3, ignore_result=True, name="pages.tasks.send_facebook_signup_conversion")
def send_facebook_signup_conversion(self, payload: Dict[str, Any]) -> None:
    """Dispatch a Sign Up conversion event to Facebook's Conversions API."""

    pixel_id = getattr(settings, "FACEBOOK_PIXEL_ID", "")
    access_token = getattr(settings, "FACEBOOK_ACCESS_TOKEN", "")

    if not (pixel_id and access_token):
        logger.debug("Facebook conversion settings incomplete; skipping event.")
        return

    event = build_conversion_event(payload)
    fb_payload = build_facebook_payload(
        event,
        pixel_id=pixel_id,
        test_event_code=getattr(settings, "FACEBOOK_TEST_EVENT_CODE", "") or None,
    )

    if not fb_payload:
        logger.debug("Facebook payload construction returned None; nothing to send.")
        return

    url = FACEBOOK_ENDPOINT_TEMPLATE.format(pixel_id=pixel_id)

    try:
        response = _post_json(url, params={"access_token": access_token}, json_payload=fb_payload)
    except requests.RequestException as exc:
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))

    if _should_retry(response.status_code):
        _log_http_error(response, "Facebook")
        raise self.retry(countdown=_retry_delay(self.request.retries))

    if response.status_code >= 400:
        _log_http_error(response, "Facebook")


@shared_task(bind=True, max_retries=3, ignore_result=True, name="pages.tasks.send_reddit_signup_conversion")
def send_reddit_signup_conversion(self, payload: Dict[str, Any]) -> None:
    """Dispatch a Sign Up conversion event to Reddit's Conversions API."""

    advertiser_id = getattr(settings, "REDDIT_ADVERTISER_ID", "") or getattr(settings, "REDDIT_PIXEL_ID", "")
    if not advertiser_id:
        logger.debug("Reddit advertiser ID not configured; skipping event.")
        return

    token = _get_reddit_access_token()
    if not token:
        logger.debug("No Reddit access token available; skipping event.")
        return

    event = build_conversion_event(payload)
    reddit_payload = build_reddit_payload(event, advertiser_id=advertiser_id)

    if not reddit_payload:
        logger.debug("Reddit payload construction returned None; nothing to send.")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": getattr(settings, "REDDIT_USER_AGENT", "gobii-platform/1.0"),
    }

    try:
        response = _post_json(REDDIT_CONVERSIONS_ENDPOINT, headers=headers, json_payload=reddit_payload)
    except requests.RequestException as exc:
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))

    if _should_retry(response.status_code):
        _log_http_error(response, "Reddit")
        raise self.retry(countdown=_retry_delay(self.request.retries))

    if response.status_code >= 400:
        _log_http_error(response, "Reddit")

