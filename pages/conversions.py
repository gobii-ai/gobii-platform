"""Helpers for building outbound conversion API payloads."""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import logging
import re
from typing import Any, Dict


logger = logging.getLogger(__name__)


def _normalized(value: Any) -> str:
    """Return a stripped, lower-cased string for hashing."""

    if value is None:
        return ""

    return str(value).strip().lower()


def _sha256(value: str) -> str:
    """Return the SHA256 hash for a value if present."""

    normalized = _normalized(value)
    if not normalized:
        return ""

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sha256_phone(phone: str) -> str:
    """Facebook expects phone numbers with digits only before hashing."""

    if not phone:
        return ""

    digits_only = re.sub(r"\D", "", phone)
    if not digits_only:
        return ""

    return hashlib.sha256(digits_only.encode("utf-8")).hexdigest()


def _drop_empty(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of mapping without empty values."""

    return {key: value for key, value in mapping.items() if value not in (None, "", [], {}, ())}


@dataclass
class ConversionEvent:
    """Structured data describing a conversion event."""

    event_name: str
    event_time: int
    event_id: str
    action_source: str = "website"
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    external_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    event_source_url: str | None = None
    fbc: str | None = None
    fbp: str | None = None
    fbclid: str | None = None
    click_ids: Dict[str, Any] = field(default_factory=dict)
    utm: Dict[str, Any] = field(default_factory=dict)
    custom_data: Dict[str, Any] = field(default_factory=dict)
    campaign: Dict[str, Any] = field(default_factory=dict)
    value: float | None = None
    currency: str | None = None


def build_facebook_payload(
    event: ConversionEvent,
    *,
    pixel_id: str,
    test_event_code: str | None = None,
) -> Dict[str, Any] | None:
    """Return a Facebook CAPI payload for the supplied event."""

    if not pixel_id:
        logger.debug("Facebook pixel ID is missing; skipping payload build.")
        return None

    user_data: Dict[str, Any] = {}

    email_hash = _sha256(event.email)
    if email_hash:
        user_data["em"] = [email_hash]

    first_name_hash = _sha256(event.first_name)
    if first_name_hash:
        user_data["fn"] = [first_name_hash]

    last_name_hash = _sha256(event.last_name)
    if last_name_hash:
        user_data["ln"] = [last_name_hash]

    phone_hash = _sha256_phone(event.phone_number or "")
    if phone_hash:
        user_data["ph"] = [phone_hash]

    external_id_hash = _sha256(event.external_id)
    if external_id_hash:
        user_data["external_id"] = external_id_hash

    if event.ip_address:
        user_data["client_ip_address"] = event.ip_address

    if event.user_agent:
        user_data["client_user_agent"] = event.user_agent

    if event.fbc:
        user_data["fbc"] = event.fbc

    if event.fbp:
        user_data["fbp"] = event.fbp

    if event.fbclid:
        user_data["subscription_id"] = event.fbclid

    custom_data = _drop_empty({**event.custom_data, **event.campaign})

    fb_event: Dict[str, Any] = {
        "event_name": event.event_name,
        "event_time": int(event.event_time),
        "event_id": event.event_id,
        "action_source": event.action_source,
        "user_data": user_data,
    }

    if event.event_source_url:
        fb_event["event_source_url"] = event.event_source_url

    if custom_data:
        fb_event["custom_data"] = custom_data

    if event.value is not None:
        fb_event.setdefault("custom_data", {})["value"] = event.value

    if event.currency:
        fb_event.setdefault("custom_data", {})["currency"] = event.currency

    payload: Dict[str, Any] = {
        "data": [fb_event],
    }

    if test_event_code:
        payload["test_event_code"] = test_event_code

    return payload


def build_reddit_payload(
    event: ConversionEvent,
    *,
    advertiser_id: str,
) -> Dict[str, Any] | None:
    """Return a Reddit Conversions API payload for the supplied event."""

    if not advertiser_id:
        logger.debug("Reddit advertiser/pixel ID is missing; skipping payload build.")
        return None

    user: Dict[str, Any] = {}

    email_hash = _sha256(event.email)
    if email_hash:
        user["email"] = email_hash

    external_id_hash = _sha256(event.external_id)
    if external_id_hash:
        user["external_id"] = external_id_hash

    if event.ip_address:
        user["ip_address"] = event.ip_address

    if event.user_agent:
        user["user_agent"] = event.user_agent

    context: Dict[str, Any] = {
        "action_source": event.action_source,
    }

    if event.click_ids:
        for key, value in event.click_ids.items():
            if value:
                context[key] = value

    reddit_event: Dict[str, Any] = {
        "event_name": event.event_name,
        "event_time": int(event.event_time),
        "event_id": event.event_id,
        "user": user,
        "context": context,
        "custom_data": _drop_empty({**event.custom_data, **event.campaign}),
    }

    if event.value is not None:
        reddit_event["conversion_value"] = event.value

    if event.currency:
        reddit_event["currency"] = event.currency

    return {
        "advertiser_id": advertiser_id,
        "events": [reddit_event],
    }


def build_conversion_event(payload: Dict[str, Any]) -> ConversionEvent:
    """Construct a ConversionEvent from a raw payload dictionary."""

    event_name = payload.get("event_name", "SignUp")
    event_time_raw = payload.get("event_time")
    if isinstance(event_time_raw, datetime):
        event_time = int(event_time_raw.timestamp())
    else:
        event_time = int(event_time_raw or datetime.utcnow().timestamp())

    return ConversionEvent(
        event_name=event_name,
        event_time=event_time,
        event_id=str(payload.get("event_id")),
        action_source=payload.get("action_source", "website"),
        email=payload.get("email"),
        first_name=payload.get("first_name"),
        last_name=payload.get("last_name"),
        phone_number=payload.get("phone_number"),
        external_id=payload.get("external_id"),
        ip_address=payload.get("ip_address"),
        user_agent=payload.get("user_agent"),
        event_source_url=payload.get("event_source_url"),
        fbc=payload.get("fbc"),
        fbp=payload.get("fbp"),
        fbclid=payload.get("fbclid"),
        click_ids=payload.get("click_ids", {}),
        utm=payload.get("utm", {}),
        custom_data=_drop_empty(payload.get("custom_data", {})),
        campaign=_drop_empty(payload.get("campaign", {})),
        value=payload.get("value"),
        currency=payload.get("currency"),
    )
