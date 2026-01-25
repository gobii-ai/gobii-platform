import uuid
import json
import hashlib
from datetime import timedelta, datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from numbers import Number
from typing import Any, Mapping
from urllib.parse import unquote

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.conf import settings
from django.db import transaction
from django.apps import apps

from allauth.account.signals import user_signed_up, user_logged_in, user_logged_out
from django.dispatch import receiver

from djstripe.models import Subscription, Customer, Invoice
from djstripe.event_handlers import djstripe_receiver
from observability import traced, trace

from config.plans import PLAN_CONFIG, get_plan_by_product_id
from config.stripe_config import get_stripe_settings
from constants.stripe import (
    ORG_OVERAGE_STATE_META_KEY,
    ORG_OVERAGE_STATE_DETACHED_PENDING,
)
from constants.plans import PlanNames
from tasks.services import TaskCreditService

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from marketing_events.api import capi
from marketing_events.context import extract_click_context
import logging
import stripe

from billing.addons import AddonEntitlementService
from billing.plan_resolver import (
    get_plan_context_for_version,
    get_plan_version_by_price_id,
    get_plan_version_by_product_id,
)
from api.models import UserBilling, OrganizationBilling, UserAttribution
from api.services.dedicated_proxy_service import (
    DedicatedProxyService,
    DedicatedProxyUnavailableError,
)
from api.services.referral_service import ReferralService
from util.payments_helper import PaymentsHelper
from util.integrations import stripe_status
from util.subscription_helper import (
    _individual_plan_product_ids,
    _individual_plan_price_ids,
    ensure_single_individual_subscription,
    get_active_subscription,
    mark_owner_billing_with_plan,
    mark_user_billing_with_plan,
    downgrade_owner_to_free_plan,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

UTM_MAPPING = {
    'source': 'utm_source',
    'medium': 'utm_medium',
    'name': 'utm_campaign',
    'content': 'utm_content',
    'term': 'utm_term'
}

CLICK_ID_PARAMS = ('gclid', 'wbraid', 'gbraid', 'msclkid', 'ttclid')


def _get_customer_with_subscriber(customer_id: str | None) -> Customer | None:
    """Fetch a Stripe customer with subscriber eagerly loaded.

    This is used by webhook handlers when the invoice payload is missing
    subscriber details but we still have a customer ID to resolve the actor.
    """
    if not customer_id:
        return None

    try:
        return Customer.objects.select_related("subscriber").filter(id=customer_id).first()
    except Exception:
        logger.debug("Failed to load customer %s for owner resolution", customer_id, exc_info=True)
        return None


def _get_stripe_data_value(container: Any, key: str) -> Any:
    """Fetch a key from Stripe payloads regardless of dict/object shape."""
    if not container:
        return None
    if isinstance(container, Mapping):
        return container.get(key)
    try:
        return getattr(container, key)
    except AttributeError:
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    """Normalise Stripe timestamps to aware datetimes."""
    if value in (None, ""):
        return None

    candidate: datetime | None = None

    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, Number):
        try:
            candidate = datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            candidate = None
    elif isinstance(value, str):
        parsed = parse_datetime(value.strip()) if value.strip() else None
        if parsed is not None:
            candidate = parsed
        else:
            try:
                candidate = datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
            except (OverflowError, OSError, ValueError):
                candidate = None

    if candidate is None:
        return None

    if timezone.is_naive(candidate):
        candidate = timezone.make_aware(candidate, timezone=dt_timezone.utc)

    return candidate


def _coerce_bool(value: Any) -> bool | None:
    """Convert Stripe boolean-ish values to strict bools."""
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return None


def _is_final_payment_attempt(invoice_payload: Mapping[str, Any] | None) -> bool | None:
    """Best-effort signal for whether Stripe will try the invoice again."""
    if not isinstance(invoice_payload, Mapping):
        return None

    next_attempt = invoice_payload.get("next_payment_attempt")
    status = (invoice_payload.get("status") or "").lower()
    auto_advance = _coerce_bool(invoice_payload.get("auto_advance"))

    if next_attempt in (None, "", 0):
        return True
    if status in {"uncollectible", "void"}:
        return True
    if auto_advance is False:
        return True
    return False


def _normalize_currency_code(currency: Any) -> str | None:
    if isinstance(currency, str):
        return currency.upper()
    return None


def _amount_major_units(*candidates: Any) -> float | None:
    """Return the first currency amount (in cents) converted to major units."""
    for cand in candidates:
        if cand is None:
            continue
        try:
            return float(Decimal(str(cand)) / Decimal("100"))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return None


_PLAN_VERSION_PRIMARY_KINDS = ("base", "seat")


def _resolve_plan_version_by_price_id(price_id: str | None):
    if not price_id:
        return None
    for kind in _PLAN_VERSION_PRIMARY_KINDS:
        plan_version = get_plan_version_by_price_id(str(price_id), kind=kind)
        if plan_version:
            return plan_version
    return None


def _resolve_plan_version_by_product_id(product_id: str | None):
    if not product_id:
        return None
    for kind in _PLAN_VERSION_PRIMARY_KINDS:
        plan_version = get_plan_version_by_product_id(str(product_id), kind=kind)
        if plan_version:
            return plan_version
    return None


def _plan_version_primary_ids() -> tuple[set[str], set[str]]:
    try:
        PlanVersionPrice = apps.get_model("api", "PlanVersionPrice")
    except Exception:
        return set(), set()
    rows = (
        PlanVersionPrice.objects
        .filter(kind__in=_PLAN_VERSION_PRIMARY_KINDS)
        .values_list("price_id", "product_id")
    )
    price_ids = {str(price_id) for price_id, _ in rows if price_id}
    product_ids = {str(product_id) for _, product_id in rows if product_id}
    return price_ids, product_ids


def _invoice_lines(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    try:
        return (payload.get("lines") or {}).get("data") or []
    except Exception as e:
        logger.exception(
            "Failed to extract invoice lines from payload: %s",
            e
        )
        return []


def _extract_plan_from_lines(lines: list[Mapping[str, Any]]) -> str | None:
    for line in lines:
        price_info = line.get("price") or {}
        if not price_info:
            price_info = (line.get("pricing") or {}).get("price_details") or {}
        price_id = price_info.get("id") or price_info.get("price")
        if price_id:
            plan_version = _resolve_plan_version_by_price_id(str(price_id))
            if plan_version:
                return plan_version.legacy_plan_code or plan_version.plan.slug
        product = price_info.get("product")
        if isinstance(product, Mapping):
            product = product.get("id")
        if product:
            plan_version = _resolve_plan_version_by_product_id(str(product))
            if plan_version:
                return plan_version.legacy_plan_code or plan_version.plan.slug
            plan = get_plan_by_product_id(product)
            if plan and plan.get("id"):
                return plan.get("id")
    return None


def _extract_subscription_id(payload: Mapping[str, Any], invoice: Invoice | None) -> str | None:
    subscription_id = None
    if invoice and getattr(invoice, "subscription", None):
        try:
            subscription_id = getattr(invoice.subscription, "id", None) or str(invoice.subscription)
        except Exception:
            subscription_id = None

    if not subscription_id:
        subscription_id = payload.get("subscription")

    if not subscription_id:
        try:
            parent = payload.get("parent") or {}
            if isinstance(parent, Mapping):
                sub_details = parent.get("subscription_details") or {}
                if isinstance(sub_details, Mapping):
                    subscription_id = sub_details.get("subscription")
        except Exception:
            subscription_id = None

    return subscription_id


def _resolve_invoice_owner(invoice: Invoice | None, payload: Mapping[str, Any]):
    customer = getattr(invoice, "customer", None) if invoice else None
    customer_id = getattr(customer, "id", None) if customer else None
    if not customer_id:
        customer_id = payload.get("customer")

    resolved_customer = customer
    if customer_id and (resolved_customer is None or not getattr(resolved_customer, "subscriber", None)):
        resolved_customer = _get_customer_with_subscriber(customer_id) or resolved_customer

    if resolved_customer and getattr(resolved_customer, "id", None):
        customer_id = getattr(resolved_customer, "id")

    owner = None
    owner_type = ""
    organization_billing: OrganizationBilling | None = None

    if resolved_customer and getattr(resolved_customer, "subscriber", None):
        owner = resolved_customer.subscriber
        owner_type = "user"
    elif customer_id:
        organization_billing = (
            OrganizationBilling.objects.select_related("organization")
            .filter(stripe_customer_id=customer_id)
            .first()
        )
        if organization_billing and organization_billing.organization:
            owner = organization_billing.organization
            owner_type = "organization"

    return owner, owner_type, organization_billing, customer_id


def _build_invoice_properties(
    payload: Mapping[str, Any],
    invoice: Invoice | None,
    *,
    customer_id: str | None,
    subscription_id: str | None,
    plan_value: str | None,
    lines: list[Mapping[str, Any]],
) -> dict[str, Any]:
    attempt_count = payload.get("attempt_count")
    attempted_flag = _coerce_bool(payload.get("attempted"))
    next_attempt_dt = _coerce_datetime(payload.get("next_payment_attempt"))
    final_attempt = _is_final_payment_attempt(payload)

    currency = _normalize_currency_code(payload.get("currency"))
    amount_due_major = _amount_major_units(payload.get("amount_due"), payload.get("total"))
    amount_paid_major = _amount_major_units(payload.get("amount_paid"))

    properties: dict[str, Any] = {
        "stripe.invoice_id": payload.get("id") or getattr(invoice, "id", None),
        "stripe.invoice_number": payload.get("number") or getattr(invoice, "number", None),
        "stripe.customer_id": customer_id,
        "stripe.subscription_id": subscription_id,
        "billing_reason": payload.get("billing_reason"),
        "collection_method": payload.get("collection_method"),
        "livemode": bool(payload.get("livemode")),
        "amount_due": amount_due_major,
        "amount_paid": amount_paid_major,
        "currency": currency,
        "attempt_number": attempt_count,
        "attempted": attempted_flag,
        "next_payment_attempt_at": next_attempt_dt,
        "final_attempt": final_attempt,
        "status": payload.get("status"),
        "customer_email": payload.get("customer_email"),
        "customer_name": payload.get("customer_name"),
        "hosted_invoice_url": payload.get("hosted_invoice_url"),
        "invoice_pdf": payload.get("invoice_pdf"),
        "line_items": len(lines) if isinstance(lines, list) else None,
        "plan": plan_value,
        "receipt_number": payload.get("receipt_number"),
    }

    status_transitions = payload.get("status_transitions") or {}
    paid_at = _coerce_datetime(status_transitions.get("paid_at"))
    finalized_at = _coerce_datetime(status_transitions.get("finalized_at"))
    if paid_at:
        properties["paid_at"] = paid_at
    if finalized_at:
        properties["finalized_at"] = finalized_at

    metadata = _coerce_metadata_dict(payload.get("metadata"))
    if metadata.get("gobii_event_id"):
        properties["gobii_event_id"] = metadata.get("gobii_event_id")

    price_ids = []
    for line in lines:
        price_info = line.get("price") or {}
        if not price_info:
            price_info = (line.get("pricing") or {}).get("price_details") or {}
        price_id = price_info.get("id") or price_info.get("price")
        if price_id:
            price_ids.append(price_id)
    if price_ids:
        properties["line_price_ids"] = price_ids

    return {k: v for k, v in properties.items() if v not in (None, "")}


def _safe_client_ip(request) -> str | None:
    """Return a normalized client IP or None if unavailable."""
    if not request:
        return None
    try:
        ip = Analytics.get_client_ip(request)
    except Exception:
        return None
    if not ip or ip == '0':
        return None
    return ip


def _build_marketing_context_from_user(user: Any) -> dict[str, Any]:
    """Construct marketing context payload from persisted attribution data."""
    context: dict[str, Any] = {"consent": True}
    if not user:
        return context

    try:
        attribution = user.attribution
    except UserAttribution.DoesNotExist:
        return context
    except AttributeError:
        return context

    click_ids: dict[str, str] = {}
    fbc = getattr(attribution, "fbc", "")
    fbclid = getattr(attribution, "fbclid", "")
    fbp = getattr(attribution, "fbp", "")

    if fbc:
        click_ids["fbc"] = fbc
    elif fbclid:
        # Synthesize fbc from fbclid if fbc is missing (improves Meta Event Match Quality)
        click_ids["fbc"] = f"fb.1.{int(timezone.now().timestamp())}.{fbclid}"
    if fbclid:
        click_ids["fbclid"] = fbclid
    if fbp:
        click_ids["fbp"] = fbp
    if click_ids:
        context["click_ids"] = click_ids

    utm_candidates = {
        "utm_source": getattr(attribution, "utm_source_last", None) or getattr(attribution, "utm_source_first", None),
        "utm_medium": getattr(attribution, "utm_medium_last", None) or getattr(attribution, "utm_medium_first", None),
        "utm_campaign": getattr(attribution, "utm_campaign_last", None) or getattr(attribution, "utm_campaign_first", None),
        "utm_content": getattr(attribution, "utm_content_last", None) or getattr(attribution, "utm_content_first", None),
        "utm_term": getattr(attribution, "utm_term_last", None) or getattr(attribution, "utm_term_first", None),
    }
    utm = {key: value for key, value in utm_candidates.items() if value}
    if utm:
        context["utm"] = utm

    last_client_ip = getattr(attribution, "last_client_ip", None)
    if last_client_ip:
        context["client_ip"] = last_client_ip

    last_user_agent = getattr(attribution, "last_user_agent", None)
    if last_user_agent:
        context["user_agent"] = last_user_agent

    return context


def _calculate_subscription_value(licensed_item: Mapping[str, Any] | None) -> tuple[float | None, str | None]:
    """Return estimated total value (in major units) and currency for the licensed item."""
    if not isinstance(licensed_item, Mapping):
        return None, None

    price = licensed_item.get("price") or {}
    if not isinstance(price, Mapping):
        price = {}

    currency = price.get("currency")
    amount = price.get("unit_amount")
    if amount is None:
        amount = price.get("unit_amount_decimal")

    quantity = licensed_item.get("quantity")
    if quantity in (None, ""):
        quantity = 1

    value: float | None = None
    if amount is not None:
        try:
            amount_dec = Decimal(str(amount))
            quantity_dec = Decimal(str(quantity))
            value = float((amount_dec * quantity_dec) / Decimal("100"))
        except (InvalidOperation, TypeError, ValueError):
            value = None

    if isinstance(currency, str):
        currency = currency.upper()

    return value, currency


def _extract_plan_value_from_subscription(source: Mapping[str, Any] | None) -> str | None:
    """Derive plan identifier from Stripe subscription payload when available."""
    if not isinstance(source, Mapping):
        return None

    try:
        items = (source.get("items") or {}).get("data", []) or []
    except AttributeError:
        items = []

    for item in items:
        if not isinstance(item, Mapping):
            continue
        plan_info = item.get("plan") or {}
        if not isinstance(plan_info, Mapping):
            continue
        if plan_info.get("usage_type") != "licensed":
            continue
        price_info = item.get("price") or {}
        if not isinstance(price_info, Mapping):
            price_info = {}
        price_id = price_info.get("id") or price_info.get("price")
        if price_id:
            plan_version = _resolve_plan_version_by_price_id(str(price_id))
            if plan_version:
                return plan_version.legacy_plan_code or plan_version.plan.slug
        product_id = price_info.get("product")
        if isinstance(product_id, Mapping):
            product_id = product_id.get("id")
        if not product_id:
            continue
        plan_version = _resolve_plan_version_by_product_id(str(product_id))
        if plan_version:
            return plan_version.legacy_plan_code or plan_version.plan.slug
        plan_config = get_plan_by_product_id(product_id)
        if not plan_config:
            continue
        plan_id = plan_config.get("id")
        if not plan_id:
            continue
        return plan_id

    return None


def _coerce_metadata_dict(candidate: Any) -> dict[str, Any]:
    """Best effort conversion of Stripe metadata containers to plain dicts."""
    if not candidate:
        return {}
    if isinstance(candidate, Mapping):
        return dict(candidate)
    try:
        return dict(candidate)
    except Exception:
        try:
            keys = list(candidate.keys())  # type: ignore[attr-defined]
        except Exception:
            return {}
        result = {}
        for key in keys:
            try:
                result[key] = candidate[key]  # type: ignore[index]
            except Exception:
                try:
                    result[key] = getattr(candidate, key)
                except Exception:
                    continue
        return result


def _get_subscription_items_data(source: Any) -> list:
    if isinstance(source, Mapping):
        items_source = source.get("items")
    else:
        items_source = getattr(source, "items", None)

    if isinstance(items_source, Mapping):
        data = items_source.get("data") or []
    else:
        data = getattr(items_source, "data", None) or []

    if data is None:
        return []
    return list(data)


def _get_quantity_for_price(source_data: Any, price_id: str) -> int:
    if not price_id:
        return 0

    for item in _get_subscription_items_data(source_data):
        if isinstance(item, Mapping):
            price = item.get("price") or {}
            item_price_id = price.get("id")
            quantity = item.get("quantity")
        else:
            price = getattr(item, "price", None)
            item_price_id = getattr(price, "id", None) if price is not None else None
            quantity = getattr(item, "quantity", None)

        if item_price_id != price_id:
            continue

        try:
            return int(quantity or 0)
        except (TypeError, ValueError):
            return 0

    return 0


def _sync_dedicated_ip_allocations(owner, owner_type: str, source_data: Any, stripe_settings) -> None:
    if owner is None:
        return

    if owner_type == "user":
        price_id = getattr(stripe_settings, "startup_dedicated_ip_price_id", "")
    else:
        price_id = getattr(stripe_settings, "org_team_dedicated_ip_price_id", "")

    if not price_id:
        return

    desired_qty = max(_get_quantity_for_price(source_data, price_id), 0)
    current_qty = DedicatedProxyService.allocated_proxies(owner).count()

    if desired_qty == current_qty:
        return

    if desired_qty > current_qty:
        missing = desired_qty - current_qty
        allocated = 0
        for _ in range(missing):
            try:
                DedicatedProxyService.allocate_proxy(owner)
                allocated += 1
            except DedicatedProxyUnavailableError:
                logger.warning(
                    "Insufficient dedicated proxies for owner %s; fulfilled %s of %s requested.",
                    getattr(owner, "id", None) or owner,
                    allocated,
                    missing,
                )
                break
    else:
        release_limit = current_qty - desired_qty
        try:
            DedicatedProxyService.release_for_owner(owner, limit=release_limit)
        except Exception:
            logger.exception(
                "Failed to release surplus dedicated proxies for owner %s",
                getattr(owner, "id", None) or owner,
            )

@receiver(user_signed_up)
def handle_user_signed_up(sender, request, user, **kwargs):
    logger.info(f"New user signed up: {user.email}")

    request.session['show_signup_tracking'] = True
    client_ip = _safe_client_ip(request)

    # Example: fire off an analytics event
    try:
        traits = {
            'first_name' : user.first_name or '',
            'last_name'  : user.last_name  or '',
            'email'      : user.email,
            'username'   : user.username or '',
            'date_joined': user.date_joined.isoformat(),
            'plan': PlanNames.FREE,
        }

        def _decode_cookie_value(raw: str | None) -> str:
            if not raw:
                return ''
            try:
                decoded = unquote(raw)
            except Exception:
                decoded = raw
            return decoded.strip().strip('"')

        utm_first_payload: dict[str, str] = {}
        utm_first_cookie = request.COOKIES.get('__utm_first')
        if utm_first_cookie:
            try:
                utm_first_payload = json.loads(utm_first_cookie)
            except json.JSONDecodeError:
                try:
                    utm_first_payload = json.loads(unquote(utm_first_cookie))
                except json.JSONDecodeError:
                    logger.exception("Failed to parse __utm_first cookie; Content: %s", utm_first_cookie)
                    utm_first_payload = {}

        current_touch = {
            utm_key: request.COOKIES.get(utm_key, '')
            for utm_key in UTM_MAPPING.values()
        }

        first_touch = {}
        for utm_key in UTM_MAPPING.values():
            preserved_value = utm_first_payload.get(utm_key)
            current_value = current_touch.get(utm_key)
            if preserved_value:
                first_touch[utm_key] = preserved_value
            elif current_value:
                first_touch[utm_key] = current_value

        last_touch = {k: v for k, v in current_touch.items() if v}

        session_first_touch = request.session.get("utm_first_touch") or {}
        session_last_touch = request.session.get("utm_last_touch") or {}
        if session_first_touch:
            for key, value in session_first_touch.items():
                if value and key not in first_touch:
                    first_touch[key] = value
        if session_last_touch:
            merged_last_touch = {k: v for k, v in session_last_touch.items() if v}
            merged_last_touch.update(last_touch)
            last_touch = merged_last_touch

        click_first_payload: dict[str, str] = {}
        click_first_cookie = request.COOKIES.get('__click_first')
        if click_first_cookie:
            try:
                click_first_payload = json.loads(click_first_cookie)
            except json.JSONDecodeError:
                try:
                    click_first_payload = json.loads(unquote(click_first_cookie))
                except json.JSONDecodeError:
                    logger.exception("Failed to parse __click_first cookie; Content: %s", click_first_cookie)
                    click_first_payload = {}

        current_click = {
            key: request.COOKIES.get(key, '')
            for key in CLICK_ID_PARAMS
        }

        first_click: dict[str, str] = {}
        for key in CLICK_ID_PARAMS:
            preserved = click_first_payload.get(key)
            current_val = current_click.get(key)
            if preserved:
                first_click[key] = preserved
            elif current_val:
                first_click[key] = current_val

        last_click = {k: v for k, v in current_click.items() if v}

        session_click_first = request.session.get("click_ids_first") or {}
        session_click_last = request.session.get("click_ids_last") or {}
        if session_click_first:
            for key, value in session_click_first.items():
                if value and key not in first_click:
                    first_click[key] = value
        if session_click_last:
            merged_last_click = {k: v for k, v in session_click_last.items() if v}
            merged_last_click.update(last_click)
            last_click = merged_last_click

        landing_first_cookie = _decode_cookie_value(request.COOKIES.get('__landing_first'))
        landing_last_cookie = _decode_cookie_value(request.COOKIES.get('landing_code'))
        landing_first = _decode_cookie_value(request.session.get('landing_code_first')) or landing_first_cookie
        landing_last = _decode_cookie_value(request.session.get('landing_code_last')) or landing_last_cookie or landing_first

        def _parse_session_timestamp(raw_value: str | None) -> datetime | None:
            if not raw_value:
                return None
            parsed = parse_datetime(raw_value)
            if parsed is None:
                return None
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone=dt_timezone.utc)
            return parsed

        first_touch_at = _parse_session_timestamp(request.session.get('landing_first_seen_at')) or timezone.now()
        last_touch_at = _parse_session_timestamp(request.session.get('landing_last_seen_at')) or timezone.now()

        fbc_cookie = _decode_cookie_value(request.COOKIES.get('_fbc'))
        fbp_cookie = _decode_cookie_value(request.COOKIES.get('_fbp'))
        fbclid_cookie = _decode_cookie_value(request.COOKIES.get('fbclid'))
        fbclid_session = request.session.get("fbclid_last") or request.session.get("fbclid_first")
        if not fbclid_cookie and fbclid_session:
            fbclid_cookie = fbclid_session

        first_referrer = _decode_cookie_value(request.COOKIES.get('first_referrer')) or (request.META.get('HTTP_REFERER') or '')
        last_referrer = _decode_cookie_value(request.COOKIES.get('last_referrer')) or (request.META.get('HTTP_REFERER') or first_referrer)
        first_path = _decode_cookie_value(request.COOKIES.get('first_path')) or request.get_full_path()
        last_path = _decode_cookie_value(request.COOKIES.get('last_path')) or request.get_full_path()

        segment_anonymous_id = _decode_cookie_value(request.COOKIES.get('ajs_anonymous_id'))
        ga_client_id = _decode_cookie_value(request.COOKIES.get('_ga'))

        # ── Referral tracking ──────────────────────────────────────────
        # Direct referral: ?ref=<code> captured into session
        referrer_code = _decode_cookie_value(request.session.get('referrer_code', ''))
        # Template share: when user signed up after viewing a shared agent template
        signup_template_code = _decode_cookie_value(request.session.get('signup_template_code', ''))

        traits.update({f'{k}_first': v for k, v in first_touch.items()})
        if last_touch:
            traits.update({f'{k}_last': v for k, v in last_touch.items()})
        traits.update({f'{k}_first': v for k, v in first_click.items()})
        if last_click:
            traits.update({f'{k}_last': v for k, v in last_click.items()})
        if landing_first:
            traits['landing_code_first'] = landing_first
        if landing_last:
            traits['landing_code_last'] = landing_last
        if fbc_cookie:
            traits['fbc'] = fbc_cookie
        if fbclid_cookie:
            traits['fbclid'] = fbclid_cookie
        if first_referrer:
            traits['first_referrer'] = first_referrer
        if last_referrer:
            traits['last_referrer'] = last_referrer
        if first_path:
            traits['first_landing_path'] = first_path
        if last_path:
            traits['last_landing_path'] = last_path
        if segment_anonymous_id:
            traits['segment_anonymous_id'] = segment_anonymous_id
        if ga_client_id:
            traits['ga_client_id'] = ga_client_id

        try:
            UserAttribution.objects.update_or_create(
                user=user,
                defaults={
                    'utm_source_first': first_touch.get('utm_source', ''),
                    'utm_medium_first': first_touch.get('utm_medium', ''),
                    'utm_campaign_first': first_touch.get('utm_campaign', ''),
                    'utm_content_first': first_touch.get('utm_content', ''),
                    'utm_term_first': first_touch.get('utm_term', ''),
                    'utm_source_last': last_touch.get('utm_source', ''),
                    'utm_medium_last': last_touch.get('utm_medium', ''),
                    'utm_campaign_last': last_touch.get('utm_campaign', ''),
                    'utm_content_last': last_touch.get('utm_content', ''),
                    'utm_term_last': last_touch.get('utm_term', ''),
                    'landing_code_first': landing_first,
                    'landing_code_last': landing_last,
                    'fbclid': fbclid_cookie,
                    'fbc': fbc_cookie,
                    'gclid_first': first_click.get('gclid', ''),
                    'gclid_last': last_click.get('gclid', ''),
                    'gbraid_first': first_click.get('gbraid', ''),
                    'gbraid_last': last_click.get('gbraid', ''),
                    'wbraid_first': first_click.get('wbraid', ''),
                    'wbraid_last': last_click.get('wbraid', ''),
                    'msclkid_first': first_click.get('msclkid', ''),
                    'msclkid_last': last_click.get('msclkid', ''),
                    'ttclid_first': first_click.get('ttclid', ''),
                    'ttclid_last': last_click.get('ttclid', ''),
                    'first_referrer': first_referrer,
                    'last_referrer': last_referrer,
                    'first_landing_path': first_path,
                    'last_landing_path': last_path,
            'segment_anonymous_id': segment_anonymous_id,
            'ga_client_id': ga_client_id,
            'first_touch_at': first_touch_at,
            'last_touch_at': last_touch_at,
            'last_client_ip': client_ip,
            'last_user_agent': request.META.get('HTTP_USER_AGENT', ''),
            'fbp': fbp_cookie,
            'referrer_code': referrer_code,
            'signup_template_code': signup_template_code,
        },
    )
        except Exception:
            logger.exception("Failed to persist user attribution for user %s", user.id)

        # ── Handle Referral ────────────────────────────────────────────
        # Process referral signup - identifies referrer and (TODO) grants credits
        if referrer_code or signup_template_code:
            try:
                ReferralService.process_signup_referral(
                    new_user=user,
                    referrer_code=referrer_code,
                    template_code=signup_template_code,
                )
            except Exception:
                logger.exception(
                    "Failed to process referral for user %s (ref=%s, template=%s)",
                    user.id,
                    referrer_code or '(none)',
                    signup_template_code or '(none)',
                )

        Analytics.identify(
            user_id=str(user.id),
            traits=traits,
        )

        # ── 2. event-specific properties & last-touch UTMs ──────
        event_id = f'reg-{uuid.uuid4()}'
        request.session['signup_event_id'] = event_id
        request.session['signup_user_id'] = str(user.id)
        normalized_email = (user.email or '').strip().lower()
        if normalized_email:
            request.session['signup_email_hash'] = hashlib.sha256(normalized_email.encode('utf-8')).hexdigest()
        else:
            request.session.pop('signup_email_hash', None)

        event_properties = {
            'plan': PlanNames.FREE,
            'date_joined': user.date_joined.isoformat(),
            **{f'{k}_first': v for k, v in first_touch.items()},
            **{f'{k}_last': v for k, v in last_touch.items()},
            **{f'{k}_first': v for k, v in first_click.items()},
            **{f'{k}_last': v for k, v in last_click.items()},
        }

        if landing_first:
            event_properties['landing_code_first'] = landing_first
        if landing_last:
            event_properties['landing_code_last'] = landing_last
        if fbc_cookie:
            event_properties['fbc'] = fbc_cookie
        if fbclid_cookie:
            event_properties['fbclid'] = fbclid_cookie
        if first_referrer:
            event_properties['first_referrer'] = first_referrer
        if last_referrer:
            event_properties['last_referrer'] = last_referrer
        if first_path:
            event_properties['first_landing_path'] = first_path
        if last_path:
            event_properties['last_landing_path'] = last_path
        if segment_anonymous_id:
            event_properties['segment_anonymous_id'] = segment_anonymous_id
        if ga_client_id:
            event_properties['ga_client_id'] = ga_client_id
        if fbc_cookie:
            event_properties['fbc'] = fbc_cookie
        if fbp_cookie:
            event_properties['fbp'] = fbp_cookie
        if fbclid_cookie:
            event_properties['fbclid'] = fbclid_cookie

        campaign_context = {}
        for key, utm_param in UTM_MAPPING.items():
            value = last_touch.get(utm_param) or first_touch.get(utm_param, '')
            if value:
                campaign_context[key] = value

        for key in CLICK_ID_PARAMS:
            value = last_click.get(key) or first_click.get(key, '')
            if value:
                campaign_context[key] = value

        if landing_last or landing_first:
            campaign_context['landing_code'] = landing_last or landing_first
        if last_referrer:
            campaign_context['referrer'] = last_referrer

        event_timestamp = timezone.now()
        event_timestamp_unix = int(event_timestamp.timestamp())

        Analytics.track(
            user_id=str(user.id),
            event=AnalyticsEvent.SIGNUP,
            properties=event_properties,
            context={
                'campaign': campaign_context,
                'userAgent': request.META.get('HTTP_USER_AGENT', ''),
            },
            ip=None,
            message_id=event_id,          # use same ID in Facebook/Reddit CAPI
            timestamp=event_timestamp
        )

        if not getattr(settings, 'GOBII_PROPRIETARY_MODE', False):
            logger.debug("Skipping conversion API enqueue because proprietary mode is disabled.")
            logger.info("Analytics tracking successful for signup.")
            return

        def enqueue_conversion_tasks():
            marketing_properties = {
                k: v
                for k, v in event_properties.items()
                if v not in (None, '', [])
            }
            marketing_properties.update(
                {
                    'event_id': event_id,
                    'event_time': event_timestamp_unix,
                }
            )
            registration_value = float(getattr(settings, "CAPI_REGISTRATION_VALUE", 0.0) or 0.0)
            marketing_properties["value"] = registration_value
            marketing_properties.setdefault("currency", "USD")
            additional_click_ids = {
                key: value
                for key in CLICK_ID_PARAMS
                if (value := (last_click.get(key) or first_click.get(key)))
            }
            marketing_context = extract_click_context(request)
            if additional_click_ids:
                marketing_context['click_ids'] = {
                    **additional_click_ids,
                    **(marketing_context.get('click_ids') or {}),
                }
            # Ensure fbc is present for Meta CAPI if we have fbclid from session/cookies
            # This improves Event Match Quality when user lands with fbclid but signs up
            # on a different page without fbclid in the URL
            click_ids = marketing_context.get('click_ids') or {}
            if not click_ids.get('fbc') and not fbc_cookie:
                # No fbc from cookies or extract_click_context, try to synthesize from fbclid
                stored_fbclid = fbclid_cookie  # includes session fallback from lines 750-753
                if stored_fbclid:
                    click_ids['fbc'] = f"fb.1.{event_timestamp_unix}.{stored_fbclid}"
                    click_ids['fbclid'] = stored_fbclid
                    marketing_context['click_ids'] = click_ids
            elif fbc_cookie and not click_ids.get('fbc'):
                # fbc exists in cookie but wasn't captured by extract_click_context
                click_ids['fbc'] = fbc_cookie
                if fbclid_cookie:
                    click_ids['fbclid'] = fbclid_cookie
                marketing_context['click_ids'] = click_ids
            utm_context = {
                **{f'{k}_first': v for k, v in first_touch.items() if v},
                **{f'{k}_last': v for k, v in last_touch.items() if v},
            }
            if utm_context:
                marketing_context['utm'] = {
                    **utm_context,
                    **(marketing_context.get('utm') or {}),
                }
            if campaign_context:
                marketing_context['campaign'] = campaign_context
            marketing_context['consent'] = True
            capi(
                user=user,
                event_name='CompleteRegistration',
                properties=marketing_properties,
                request=None,
                context=marketing_context,
            )

        transaction.on_commit(enqueue_conversion_tasks)

        logger.info("Analytics tracking successful for signup.")
    except Exception as e:
        logger.exception("Analytics tracking failed during signup.")

@receiver(user_logged_in)
def handle_user_logged_in(sender, request, user, **kwargs):
    logger.info(f"User logged in: {user.id} ({user.email})")

    try:
        client_ip = _safe_client_ip(request)
        if client_ip:
            UserAttribution.objects.update_or_create(
                user=user,
                defaults={'last_client_ip': client_ip},
            )
        Analytics.identify(user.id, {
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'email': user.email,
            'username': user.username or '',
            'date_joined': user.date_joined,
        })
        Analytics.track_event(
            user_id=user.id,
            event=AnalyticsEvent.LOGGED_IN,
            source=AnalyticsSource.WEB,
            properties={}
        )
        logger.info("Analytics tracking successful for login.")
    except Exception:
        logger.exception("Analytics tracking failed during login.")

@receiver(user_logged_out)
def handle_user_logged_out(sender, request, user, **kwargs):
    logger.info(f"User logged out: {user.id} ({user.email})")

    try:
        Analytics.track_event(
            user_id=user.id,
            event=AnalyticsEvent.LOGGED_OUT,
            source=AnalyticsSource.WEB,
            properties={}
        )
        logger.info("Analytics tracking successful for logout.")
    except Exception:
        logger.exception("Analytics tracking failed during logout.")


@djstripe_receiver(["invoice.payment_failed"])
def handle_invoice_payment_failed(event, **kwargs):
    """Emit analytics when Stripe fails to collect payment for an invoice."""
    with tracer.start_as_current_span("handle_invoice_payment_failed") as span:
        payload = event.data.get("object", {}) or {}
        if payload.get("object") != "invoice":
            span.add_event("unexpected_object", {"object": payload.get("object")})
            logger.info("Invoice payment failed webhook received non-invoice payload")
            return

        status = stripe_status()
        if not status.enabled:
            span.add_event("stripe_disabled")
            logger.info("Stripe disabled; ignoring invoice payment failed webhook %s", payload.get("id"))
            return

        stripe_key = PaymentsHelper.get_stripe_key()
        if not stripe_key:
            span.add_event("stripe_key_missing")
            logger.warning("Stripe key unavailable; ignoring invoice payment failed webhook %s", payload.get("id"))
            return

        stripe.api_key = stripe_key

        invoice = None
        try:
            invoice = Invoice.sync_from_stripe_data(payload)
        except Exception:
            span.add_event("invoice_sync_failed")
            logger.exception("Failed to sync invoice %s from webhook", payload.get("id"))

        owner, owner_type, _organization_billing, customer_id = _resolve_invoice_owner(invoice, payload)

        if owner_type:
            span.set_attribute("invoice.owner.type", owner_type)
        if owner:
            span.set_attribute("invoice.owner.id", str(getattr(owner, "id", "")))
        if not owner:
            span.add_event("owner_not_found", {"customer.id": customer_id})

        subscription_id = _extract_subscription_id(payload, invoice)
        lines = _invoice_lines(payload)
        plan_value = _extract_plan_from_lines(lines)

        properties = _build_invoice_properties(
            payload,
            invoice,
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan_value=plan_value,
            lines=lines,
        )

        properties = Analytics.with_org_properties(
            properties,
            organization=owner if owner_type == "organization" else None,
            organization_flag=owner_type == "organization",
        )

        try:
            if owner_type == "user" and owner:
                track_user_id = getattr(owner, "id", None)

                Analytics.track_event(
                    user_id=track_user_id,
                    event=AnalyticsEvent.BILLING_PAYMENT_FAILED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            elif owner_type == "organization" and owner:
                track_user_id = getattr(owner, "created_by_id", None)
                if track_user_id:
                    Analytics.track_event(
                        user_id=track_user_id,
                        event=AnalyticsEvent.BILLING_PAYMENT_FAILED,
                        source=AnalyticsSource.API,
                        properties=properties,
                    )
                elif customer_id:
                    Analytics.track_event_anonymous(
                        anonymous_id=str(customer_id),
                        event=AnalyticsEvent.BILLING_PAYMENT_FAILED,
                        source=AnalyticsSource.API,
                        properties=properties,
                    )
            elif customer_id:
                Analytics.track_event_anonymous(
                    anonymous_id=str(customer_id),
                    event=AnalyticsEvent.BILLING_PAYMENT_FAILED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            else:
                span.add_event("analytics_skipped_no_actor")
                logger.info("Skipping analytics for invoice %s: no user or customer context", payload.get("id"))
        except Exception:
            span.add_event("analytics_failure")
            logger.exception("Failed to track invoice.payment_failed for invoice %s", payload.get("id"))


@djstripe_receiver(["invoice.payment_succeeded"])
def handle_invoice_payment_succeeded(event, **kwargs):
    """Emit analytics when Stripe successfully collects payment for an invoice."""
    with tracer.start_as_current_span("handle_invoice_payment_succeeded") as span:
        payload = event.data.get("object", {}) or {}
        if payload.get("object") != "invoice":
            span.add_event("unexpected_object", {"object": payload.get("object")})
            logger.info("Invoice payment succeeded webhook received non-invoice payload")
            return

        status = stripe_status()
        if not status.enabled:
            span.add_event("stripe_disabled")
            logger.info("Stripe disabled; ignoring invoice payment succeeded webhook %s", payload.get("id"))
            return

        stripe_key = PaymentsHelper.get_stripe_key()
        if not stripe_key:
            span.add_event("stripe_key_missing")
            logger.warning("Stripe key unavailable; ignoring invoice payment succeeded webhook %s", payload.get("id"))
            return

        stripe.api_key = stripe_key

        invoice = None
        try:
            invoice = Invoice.sync_from_stripe_data(payload)
        except Exception:
            span.add_event("invoice_sync_failed")
            logger.exception("Failed to sync invoice %s from webhook", payload.get("id"))

        owner, owner_type, _organization_billing, customer_id = _resolve_invoice_owner(invoice, payload)

        if owner_type:
            span.set_attribute("invoice.owner.type", owner_type)
        if owner:
            span.set_attribute("invoice.owner.id", str(getattr(owner, "id", "")))
        if not owner:
            span.add_event("owner_not_found", {"customer.id": customer_id})

        subscription_id = _extract_subscription_id(payload, invoice)
        lines = _invoice_lines(payload)
        plan_value = _extract_plan_from_lines(lines)

        properties = _build_invoice_properties(
            payload,
            invoice,
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan_value=plan_value,
            lines=lines,
        )

        properties = Analytics.with_org_properties(
            properties,
            organization=owner if owner_type == "organization" else None,
            organization_flag=owner_type == "organization",
        )

        try:
            if owner_type == "user" and owner:
                track_user_id = getattr(owner, "id", None)

                Analytics.track_event(
                    user_id=track_user_id,
                    event=AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            elif owner_type == "organization" and owner:
                track_user_id = getattr(owner, "created_by_id", None)
                if track_user_id:
                    Analytics.track_event(
                        user_id=track_user_id,
                        event=AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED,
                        source=AnalyticsSource.API,
                        properties=properties,
                    )
                elif customer_id:
                    Analytics.track_event_anonymous(
                        anonymous_id=str(customer_id),
                        event=AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED,
                        source=AnalyticsSource.API,
                        properties=properties,
                    )
            elif customer_id:
                Analytics.track_event_anonymous(
                    anonymous_id=str(customer_id),
                    event=AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            else:
                span.add_event("analytics_skipped_no_actor")
                logger.info("Skipping analytics for invoice %s: no user or customer context", payload.get("id"))
        except Exception:
            span.add_event("analytics_failure")
            logger.exception("Failed to track invoice.payment_succeeded for invoice %s", payload.get("id"))

@djstripe_receiver(["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"])
def handle_subscription_event(event, **kwargs):
    """Update user status and quota based on subscription events."""
    with tracer.start_as_current_span("handle_subscription_event") as span:
        payload = event.data.get("object", {})

        # 1. Ignore anything that isn't a subscription (defensive, though Stripe shouldn't send it)
        if payload.get("object") != "subscription":
            span.add_event('Ignoring non-subscription event')
            logger.warning("Unexpected Stripe object in webhook: %s", payload.get("object"))
            return

        status = stripe_status()
        if not status.enabled:
            span.add_event('Stripe disabled; ignoring webhook')
            logger.info("Stripe disabled; ignoring subscription webhook %s", payload.get("id"))
            return

        stripe_key = PaymentsHelper.get_stripe_key()
        if not stripe_key:
            span.add_event('Stripe key missing; ignoring webhook')
            logger.warning("Stripe key unavailable; ignoring subscription webhook %s", payload.get("id"))
            return

        stripe.api_key = stripe_key

        # Note: do not early-return on hard-deleted payloads; we still need to
        # downgrade the user to the free plan when a subscription is deleted.
        stripe_sub = None

        # 3. Normal create/update flow
        try:
            sub = Subscription.sync_from_stripe_data(payload)  # first try the cheap way
        except Exception as exc:
            logger.error("Failed to sync subscription data %s", exc)
            if "auto_paging_iter" in str(exc):
                # Fallback – pick ONE of the fixes above
                stripe_sub = stripe.Subscription.retrieve(  # or construct_from(...)
                    payload["id"],
                    expand=["items"],
                )

                sub = Subscription.sync_from_stripe_data(stripe_sub)
            else:
                logger.error("Failed to sync subscription data %s", exc)
                # TODO: Consider a more robust fallback or retry mechanism here if needed
                # For now, re-raising the exception might be acceptable if sync is critical
                raise

        customer: Customer | None = sub.customer
        if not customer:
            span.add_event('Ignoring subscription with no customer')
            logger.info("Subscription %s has no linked customer; nothing to do.", sub.id)
            return

        span.set_attribute('subscription.customer.id', getattr(customer, 'id', ''))
        span.set_attribute('subscription.customer.email', getattr(customer, 'email', ''))

        owner = None
        owner_type = ""
        organization_billing: OrganizationBilling | None = None

        if customer.subscriber:
            owner = customer.subscriber
            owner_type = "user"
        else:
            organization_billing = (
                OrganizationBilling.objects.select_related("organization")
                .filter(stripe_customer_id=customer.id)
                .first()
            )
            if organization_billing and organization_billing.organization:
                owner = organization_billing.organization
                owner_type = "organization"

        if not owner:
            span.add_event('Ignoring subscription event with no owner')
            logger.info("Subscription %s has no linked billing owner; nothing to do.", sub.id)
            return

        span.set_attribute('subscription.owner.type', owner_type)

        subscription_id = getattr(sub, "id", None)
        marketing_context: dict[str, Any]
        plan_before_cancellation = None
        if owner_type == "user":
            marketing_context = _build_marketing_context_from_user(owner)

            try:
                plan_before_cancellation = owner.billing.subscription  # type: ignore[attr-defined]
            except UserBilling.DoesNotExist:
                plan_before_cancellation = None
            except AttributeError:
                plan_before_cancellation = None
        else:
            marketing_context = {}

        source_data = stripe_sub if stripe_sub is not None else (getattr(sub, "stripe_data", {}) or {})

        # Handle explicit deletions (downgrade to free immediately)
        try:
            event_type = getattr(event, "type", "") or getattr(event, "event_type", "")
        except Exception:
            event_type = ""

        span.set_attribute('subscription.event_type', event_type)

        # Guardrail: when Stripe fires a new individual (non-org) subscription, ensure we reuse one subscription
        # per customer and cancel any older duplicates. This preserves add-ons (e.g., dedicated IPs/meters) on the newest sub.
        try:
            if event_type == "customer.subscription.created" and owner_type == "user":
                items_data = []
                if isinstance(source_data, Mapping):
                    items_data = ((source_data.get("items") or {}).get("data") or [])

                plan_products = _individual_plan_product_ids()
                plan_price_ids = _individual_plan_price_ids()
                licensed_price_id = None
                metered_price_id = None

                for item in items_data:
                    price = item.get("price") or {}
                    product = price.get("product")
                    if isinstance(product, Mapping):
                        product = product.get("id")

                    usage_type = price.get("usage_type") or (price.get("recurring") or {}).get("usage_type")

                    if not licensed_price_id and (
                        (product and product in plan_products)
                        or (price.get("id") and price.get("id") in plan_price_ids)
                    ):
                        licensed_price_id = price.get("id")
                    elif not metered_price_id and usage_type == "metered":
                        metered_price_id = price.get("id")

                customer_id = getattr(customer, "id", None)
                if licensed_price_id and customer_id:
                    ensure_single_individual_subscription(
                        customer_id=str(customer_id),
                        licensed_price_id=licensed_price_id,
                        metered_price_id=metered_price_id,
                        metadata=source_data.get("metadata") if isinstance(source_data, Mapping) else {},
                        idempotency_key=f"sub-webhook-upsert-{payload.get('id', '')}",
                        create_if_missing=False,
                    )
        except Exception:
            logger.warning(
                "Failed to ensure single individual subscription for customer %s during webhook",
                getattr(customer, "id", None),
                exc_info=True,
            )

        if event_type == "customer.subscription.deleted" or getattr(sub, "status", "") == "canceled":
            active_sub = get_active_subscription(owner)
            if active_sub and getattr(active_sub, "id", None) and getattr(active_sub, "id", None) != subscription_id:
                span.add_event(
                    "subscription.cancel_ignored_active_subscription",
                    {
                        "subscription.id": subscription_id or "",
                        "active_subscription.id": getattr(active_sub, "id", "") or "",
                    },
                )
                logger.info(
                    "Skipping downgrade for owner %s: subscription %s canceled but active subscription %s exists.",
                    getattr(owner, "id", None) or owner,
                    subscription_id,
                    getattr(active_sub, "id", None),
                )
                return

            downgrade_owner_to_free_plan(owner)

            try:
                DedicatedProxyService.release_for_owner(owner)
            except Exception:
                logger.exception(
                    "Failed to release dedicated proxies for owner %s during cancellation",
                    getattr(owner, "id", None) or owner,
                )

            if owner_type == "user":
                try:
                    Analytics.identify(
                        owner.id,
                        {
                            'plan': PlanNames.FREE,
                        },
                    )
                except Exception:
                    logger.exception("Failed to update user subscription in analytics for user %s", owner.id)

                try:
                    Analytics.track_event(
                        user_id=owner.id,
                        event=AnalyticsEvent.SUBSCRIPTION_CANCELLED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'plan': PlanNames.FREE,
                            'stripe.subscription_id': getattr(sub, 'id', None),
                        },
                    )
                except Exception:
                    logger.exception("Failed to track subscription cancellation for user %s", owner.id)

                try:
                    cancel_plan_value = _extract_plan_value_from_subscription(source_data) or plan_before_cancellation
                    cancel_properties = {
                        "plan": cancel_plan_value or PlanNames.FREE,
                        "subscription_id": subscription_id,
                        "status": "canceled",
                        "churn_stage": "voluntary",
                    }
                    cancel_properties = {k: v for k, v in cancel_properties.items() if v is not None}
                    capi(
                        user=owner,
                        event_name="CancelSubscription",
                        properties=cancel_properties,
                        request=None,
                        context=marketing_context,
                    )
                except Exception:
                    logger.exception(
                        "Failed to enqueue marketing cancellation event for user %s",
                        getattr(owner, "id", None),
                    )
            else:
                billing = organization_billing
                if billing:
                    updates: list[str] = []
                    if billing.purchased_seats != 0:
                        billing.purchased_seats = 0
                        updates.append("purchased_seats")
                    if getattr(billing, "stripe_subscription_id", None):
                        billing.stripe_subscription_id = None
                        updates.append("stripe_subscription_id")
                    if getattr(billing, "cancel_at", None):
                        billing.cancel_at = None
                        updates.append("cancel_at")
                    if getattr(billing, "cancel_at_period_end", False):
                        billing.cancel_at_period_end = False
                        updates.append("cancel_at_period_end")
                    if updates:
                        billing.save(update_fields=updates)
            return

        # Prefer explicit Stripe retrieve when present; otherwise use dj-stripe's cached payload
        # from the Subscription row. This allows the normal sync_from_stripe_data path to work.

        subscription_metadata: dict[str, Any] = {}
        if isinstance(source_data, Mapping):
            subscription_metadata = _coerce_metadata_dict(source_data.get("metadata"))
        if not subscription_metadata:
            subscription_metadata = _coerce_metadata_dict(getattr(sub, "metadata", None))

        current_period_start_dt = _coerce_datetime(_get_stripe_data_value(source_data, "current_period_start"))
        current_period_end_dt = _coerce_datetime(_get_stripe_data_value(source_data, "current_period_end"))
        cancel_at_dt = _coerce_datetime(_get_stripe_data_value(source_data, "cancel_at"))
        cancel_at_period_end_flag = _coerce_bool(_get_stripe_data_value(source_data, "cancel_at_period_end"))

        span.set_attribute('subscription.current_period_start', str(current_period_start_dt))
        span.set_attribute('subscription.current_period_end', str(current_period_end_dt))
        span.set_attribute('subscription.cancel_at', str(cancel_at_dt))
        span.set_attribute('subscription.cancel_at_period_end', str(cancel_at_period_end_flag))

        if current_period_end_dt is None:
            current_period_end_dt = _coerce_datetime(getattr(sub, "current_period_end", None))
            span.set_attribute('subscription.current_period_end_fallback', str(current_period_end_dt))

        if cancel_at_dt is None:
            cancel_at_dt = _coerce_datetime(getattr(sub, "cancel_at", None))
            span.set_attribute('subscription.cancel_at_fallback', str(cancel_at_dt))
        if cancel_at_period_end_flag is None:
            cancel_at_period_end_flag = _coerce_bool(getattr(sub, "cancel_at_period_end", None))
            span.set_attribute('subscription.cancel_at_period_end_fallback', str(cancel_at_period_end_flag))

        invoice_id = _get_stripe_data_value(source_data, "latest_invoice") or getattr(sub, "latest_invoice", None)
        span.set_attribute('subscription.invoice_id', str(invoice_id))

        billing_reason = _get_stripe_data_value(source_data, "billing_reason")
        if billing_reason is None:
            billing_reason = getattr(sub, "billing_reason", None)

        if invoice_id and not billing_reason:
            try:
                invoice_data = stripe.Invoice.retrieve(invoice_id)
                invoice = Invoice.sync_from_stripe_data(invoice_data)
                billing_reason = getattr(invoice, "billing_reason", None)
                if billing_reason is None:
                    billing_reason = _get_stripe_data_value(getattr(invoice, "stripe_data", {}) or {}, "billing_reason")
            except Exception as exc:
                span.add_event('invoice.fetch_failed', {'invoice.id': invoice_id})
                logger.warning(
                    "Webhook: failed to fetch invoice %s for subscription %s: %s",
                    invoice_id,
                    getattr(sub, 'id', ''),
                    exc,
                )

        # Locate the licensed (base plan) item among subscription items (prefer price.usage_type)
        def _item_usage_type(item: dict) -> str:
            price = item.get("price") or {}
            recurring = price.get("recurring") or {}
            return (
                price.get("usage_type")
                or recurring.get("usage_type")
                or (item.get("plan") or {}).get("usage_type")
                or ""
            )

        plan_price_ids, plan_product_ids = _plan_version_primary_ids()
        plan_products = {str(cfg.get("product_id")) for cfg in PLAN_CONFIG.values() if cfg.get("product_id")}
        plan_products |= plan_product_ids

        licensed_item = None
        fallback_item = None
        try:
            for item in source_data.get("items", {}).get("data", []) or []:
                usage_type = _item_usage_type(item).lower()
                price = item.get("price") or {}
                price_id = price.get("id") or price.get("price")
                product = price.get("product")
                if isinstance(product, Mapping):
                    product = product.get("id")

                if price_id and str(price_id) in plan_price_ids:
                    licensed_item = item
                    break

                if product and str(product) in plan_products:
                    licensed_item = item
                    break

                if usage_type == "metered":
                    continue

                if fallback_item is None:
                    fallback_item = item
        except Exception as e:
            logger.warning("Webhook: failed to inspect subscription items for %s: %s", sub.id, e)

        if licensed_item is None and fallback_item is not None:
            licensed_item = fallback_item

        # Proceed only when the subscription is active and we found a licensed item
        span.set_attribute('subscription.status', str(sub.status))
        if sub.status == 'active' and licensed_item is not None:
            price_info = licensed_item.get("price") or {}
            if not isinstance(price_info, Mapping):
                price_info = {}

            price_id = price_info.get("id") or price_info.get("price")
            product_id = price_info.get("product")
            if isinstance(product_id, Mapping):
                product_id = product_id.get("id")

            plan_kind = "seat" if owner_type == "organization" else "base"
            plan_version = get_plan_version_by_price_id(str(price_id), kind=plan_kind) if price_id else None
            if not plan_version and product_id:
                plan_version = get_plan_version_by_product_id(str(product_id), kind=plan_kind)

            plan = get_plan_context_for_version(plan_version) if plan_version else None
            if not plan and product_id:
                plan = get_plan_by_product_id(product_id)

            invoice_id = source_data.get("latest_invoice")

            plan_value = None
            if plan_version:
                plan_value = plan_version.legacy_plan_code or plan_version.plan.slug
            if not plan_value and plan:
                plan_value = plan.get("id")
            if not plan_value:
                plan_value = PlanNames.FREE
            if not plan:
                plan = PLAN_CONFIG.get(PlanNames.FREE)

            items_data: list[Mapping[str, Any]] = []
            try:
                items_data = ((source_data.get("items") or {}).get("data") or []) if isinstance(source_data, Mapping) else []
            except Exception:
                items_data = []

            try:
                AddonEntitlementService.sync_subscription_entitlements(
                    owner=owner,
                    owner_type=owner_type,
                    plan_id=plan_value,
                    plan_version=plan_version,
                    subscription_items=items_data,
                    period_start=current_period_start_dt or timezone.now(),
                    period_end=current_period_end_dt,
                    created_via="subscription_webhook",
                )
            except Exception:
                logger.exception(
                    "Failed to sync add-on entitlements for owner %s during subscription webhook",
                    getattr(owner, "id", None) or owner,
                )

            stripe_settings = get_stripe_settings()

            if owner_type == "user":
                mark_user_billing_with_plan(owner, plan_value, update_anchor=False, plan_version=plan_version)
                should_grant = billing_reason in {"subscription_create", "subscription_cycle"}
                if billing_reason is None and event_type == "customer.subscription.created":
                    should_grant = True
                if should_grant:
                    TaskCreditService.grant_subscription_credits(
                        owner,
                        plan=plan,
                        invoice_id=invoice_id or ""
                    )

                try:
                    ub = owner.billing
                    if current_period_start_dt:
                        new_day = current_period_start_dt.day
                        if ub.billing_cycle_anchor != new_day:
                            ub.billing_cycle_anchor = new_day
                            ub.save(update_fields=["billing_cycle_anchor"])
                except UserBilling.DoesNotExist as ue:
                    logger.exception("UserBilling record not found for user %s during anchor alignment: %s", owner.id, ue)
                except Exception as e:
                    logger.exception("Failed to align billing anchor with Stripe period for user %s: %s", owner.id, e)

                Analytics.identify(owner.id, {
                    'plan': plan_value,
                })

                event_properties = {
                    'plan': plan_value,
                }
                if invoice_id:
                    event_properties['stripe.invoice_id'] = invoice_id

                analytics_event = None
                if billing_reason == 'subscription_create':
                    analytics_event = AnalyticsEvent.SUBSCRIPTION_CREATED
                elif billing_reason == 'subscription_cycle':
                    analytics_event = AnalyticsEvent.SUBSCRIPTION_RENEWED

                suppress_marketing_event = False
                if (
                    analytics_event == AnalyticsEvent.SUBSCRIPTION_CREATED
                    and event_type != "customer.subscription.created"
                ):
                    suppress_marketing_event = True

                if analytics_event:
                    Analytics.track_event(
                        user_id=owner.id,
                        event=analytics_event,
                        source=AnalyticsSource.WEB,
                        properties=event_properties,
                    )

                    marketing_properties = {
                        "plan": plan_value,
                        "subscription_id": subscription_id,
                    }
                    if analytics_event == AnalyticsEvent.SUBSCRIPTION_CREATED:
                        event_id_override = subscription_metadata.get("gobii_event_id")
                        if isinstance(event_id_override, str) and event_id_override.strip():
                            marketing_properties["event_id"] = event_id_override.strip()
                    value, currency = _calculate_subscription_value(licensed_item)
                    ltv_multiple = float(getattr(settings, "CAPI_LTV_MULTIPLE", 1.0) or 1.0)
                    if value is not None:
                        marketing_properties["value"] = value * ltv_multiple
                    if currency:
                        marketing_properties["currency"] = currency
                    if analytics_event == AnalyticsEvent.SUBSCRIPTION_RENEWED:
                        marketing_properties["renewal"] = True

                    marketing_properties = {k: v for k, v in marketing_properties.items() if v is not None}

                    if not suppress_marketing_event:
                        try:
                            if analytics_event != AnalyticsEvent.SUBSCRIPTION_RENEWED:
                                capi(
                                    user=owner,
                                    event_name="Subscribe",
                                    properties=marketing_properties,
                                    request=None,
                                    context=marketing_context,
                                )
                            # Renewal marketing events temporarily disabled.
                        except Exception:
                            logger.exception(
                                "Failed to enqueue marketing subscription event for user %s",
                                getattr(owner, "id", None),
                            )
            else:
                seats = 0
                try:
                    seats = int(licensed_item.get("quantity") or 0)
                except (TypeError, ValueError):
                    seats = 0

                prev_seats = 0
                if organization_billing:
                    prev_seats = getattr(organization_billing, "purchased_seats", 0)

                overage_price_id = stripe_settings.org_team_additional_task_price_id
                if overage_price_id:
                    items_data = source_data.get("items", {}).get("data", []) or []
                    has_overage_item = any(
                        (item.get("price") or {}).get("id") == overage_price_id
                        for item in items_data
                    )

                    metadata: dict[str, str] = dict(subscription_metadata)
                    overage_state = metadata.get(ORG_OVERAGE_STATE_META_KEY, "")
                    seat_delta = seats - prev_seats

                    should_reattach = not has_overage_item and (
                        overage_state != ORG_OVERAGE_STATE_DETACHED_PENDING or seat_delta != 0
                    )

                    if should_reattach:
                        subscription_id = getattr(sub, "id", "")
                        already_present = False
                        try:
                            live_subscription = stripe.Subscription.retrieve(
                                subscription_id,
                                expand=["items.data.price"],
                            )
                            live_items = (live_subscription.get("items") or {}).get("data", []) if isinstance(live_subscription, Mapping) else []
                            already_present = any(
                                (item.get("price") or {}).get("id") == overage_price_id
                                for item in live_items or []
                            )
                        except Exception as exc:  # pragma: no cover - unexpected Stripe error
                            logger.warning(
                                "Failed to refresh subscription %s before reattaching overage SKU: %s",
                                subscription_id,
                                exc,
                            )

                        if not already_present:
                            try:
                                stripe.SubscriptionItem.create(
                                    subscription=subscription_id,
                                    price=overage_price_id,
                                )
                                span.add_event(
                                    "org_subscription_overage_item_added",
                                    {
                                        "subscription.id": subscription_id,
                                        "price.id": overage_price_id,
                                    },
                                )
                            except stripe.error.InvalidRequestError as exc:
                                logger.warning(
                                    "Overage price %s already present on subscription %s when reattaching: %s",
                                    overage_price_id,
                                    subscription_id,
                                    exc,
                                )
                                already_present = True
                            except Exception as exc:  # pragma: no cover - unexpected Stripe error
                                logger.exception(
                                    "Failed to attach org overage price %s to subscription %s: %s",
                                    overage_price_id,
                                    subscription_id,
                                    exc,
                                )
                        else:
                            span.add_event(
                                "org_subscription_overage_item_exists",
                                {
                                    "subscription.id": subscription_id,
                                    "price.id": overage_price_id,
                                },
                            )

                        if (overage_state == ORG_OVERAGE_STATE_DETACHED_PENDING) and (already_present or not should_reattach):
                            try:
                                stripe.Subscription.modify(
                                    subscription_id,
                                    metadata={ORG_OVERAGE_STATE_META_KEY: ""},
                                )
                            except Exception as exc:  # pragma: no cover - unexpected Stripe error
                                logger.warning(
                                    "Failed to clear overage detach flag on subscription %s: %s",
                                    subscription_id,
                                    exc,
                                )
                    elif has_overage_item and overage_state == ORG_OVERAGE_STATE_DETACHED_PENDING:
                        try:
                            stripe.Subscription.modify(
                                getattr(sub, "id", ""),
                                metadata={ORG_OVERAGE_STATE_META_KEY: ""},
                            )
                        except Exception as exc:  # pragma: no cover - unexpected Stripe error
                            logger.warning(
                                "Failed to clear overage detach flag on subscription %s: %s",
                                getattr(sub, "id", ""),
                                exc,
                            )

                billing = mark_owner_billing_with_plan(
                    owner,
                    plan_value,
                    update_anchor=False,
                    plan_version=plan_version,
                )
                if billing:
                    updates: list[str] = []
                    if current_period_start_dt:
                        new_day = current_period_start_dt.day
                        if billing.billing_cycle_anchor != new_day:
                            billing.billing_cycle_anchor = new_day
                            updates.append("billing_cycle_anchor")

                    new_subscription_id = getattr(sub, 'id', None)
                    if getattr(billing, 'stripe_subscription_id', None) != new_subscription_id:
                        billing.stripe_subscription_id = new_subscription_id
                        updates.append("stripe_subscription_id")

                    if seats and getattr(billing, 'purchased_seats', None) != seats:
                        billing.purchased_seats = seats
                        updates.append("purchased_seats")

                    pending_schedule_id = getattr(billing, "pending_seat_schedule_id", "")
                    if pending_schedule_id and seats != prev_seats:
                        billing.pending_seat_quantity = None
                        billing.pending_seat_effective_at = None
                        billing.pending_seat_schedule_id = ""
                        for field in (
                            "pending_seat_quantity",
                            "pending_seat_effective_at",
                            "pending_seat_schedule_id",
                        ):
                            if field not in updates:
                                updates.append(field)

                    if hasattr(billing, 'cancel_at'):
                        if billing.cancel_at != cancel_at_dt:
                            billing.cancel_at = cancel_at_dt
                            updates.append("cancel_at")

                    if hasattr(billing, 'cancel_at_period_end'):
                        if cancel_at_period_end_flag is not None and billing.cancel_at_period_end != cancel_at_period_end_flag:
                            billing.cancel_at_period_end = cancel_at_period_end_flag
                            updates.append("cancel_at_period_end")

                    if updates:
                        billing.save(update_fields=updates)

                if seats > 0:
                    seats_to_grant = 0
                    if billing_reason in {"subscription_create", "subscription_cycle"}:
                        if billing_reason == "subscription_create" and prev_seats > 0:
                            seats_to_grant = max(seats - prev_seats, 0)
                        else:
                            seats_to_grant = seats
                    elif billing_reason == "subscription_update" and seats > prev_seats:
                        seats_to_grant = seats - prev_seats

                    if seats_to_grant > 0:
                        grant_invoice_id = ""
                        if invoice_id and (
                            billing_reason == "subscription_cycle"
                            or (billing_reason == "subscription_create" and prev_seats == 0)
                        ):
                            grant_invoice_id = invoice_id

                        # For cycle starts we want to reset the active monthly block
                        # instead of stacking an extra TaskCredit record.
                        replace_current = source_data.get("billing_reason") in {"subscription_create", "subscription_cycle"}

                        TaskCreditService.grant_subscription_credits_for_organization(
                            owner,
                            seats=seats_to_grant,
                            plan=plan,
                            invoice_id=grant_invoice_id,
                            subscription=sub,
                            replace_current=replace_current,
                        )

            _sync_dedicated_ip_allocations(owner, owner_type, source_data, stripe_settings)
