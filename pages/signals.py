import uuid
import json
from datetime import timedelta, datetime, timezone as dt_timezone
from numbers import Number
from typing import Any, Mapping

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from allauth.account.signals import user_signed_up, user_logged_in, user_logged_out
from django.dispatch import receiver

from djstripe.models import Subscription, Customer, Invoice
from djstripe.event_handlers import djstripe_receiver
from observability import traced, trace

from config.plans import get_plan_by_product_id
from constants.plans import PlanNamesChoices
from tasks.services import TaskCreditService

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
import logging
import stripe

from api.models import UserBilling, OrganizationBilling
from util.payments_helper import PaymentsHelper
from util.subscription_helper import (
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
    if isinstance(value, Number):
        return bool(value)
    return None

@receiver(user_signed_up)
def handle_user_signed_up(sender, request, user, **kwargs):
    logger.info(f"New user signed up: {user.email}")

    request.session['show_signup_tracking'] = True

    # Example: fire off an analytics event
    try:
        traits = {
            'first_name' : user.first_name or '',
            'last_name'  : user.last_name  or '',
            'email'      : user.email,
            'username'   : user.username or '',
            'date_joined': user.date_joined.isoformat(),
        }

        # first-touch UTMs (if you stored them in a cookie)
        first_touch = {k: request.COOKIES.get(k, '') for k in UTM_MAPPING.values()}
        traits.update({f'{k}_first': v for k, v in first_touch.items() if v})

        utm_first_cookie = request.COOKIES.get('__utm_first')
        if utm_first_cookie:
            try:
                utm_first = json.loads(utm_first_cookie)
                for k, v in utm_first.items():
                    if k in UTM_MAPPING.values() and not first_touch.get(k) and v:
                        traits[f'{k}_first'] = v
            except json.JSONDecodeError:
                logger.exception("Failed to parse __utm_first cookie; Content: %s", utm_first_cookie)

        Analytics.identify(
            user_id=str(user.id),
            traits=traits,
        )

        # ── 2. event-specific properties & last-touch UTMs ──────
        last_touch = {}


        for key, utm_param in UTM_MAPPING.items():
            value = request.COOKIES.get(utm_param, '') or traits.get(f'{utm_param}_first', '')
            last_touch[key] = value

        event_id = f'reg-{uuid.uuid4()}'

        event_properties = {
            'plan': 'free',
            'date_joined': user.date_joined.isoformat(),
            **{k: v for k, v in last_touch.items() if v},
        }

        Analytics.track(
            user_id=str(user.id),
            event=AnalyticsEvent.SIGNUP,
            properties=event_properties,
            context={
                'campaign': last_touch,
                'userAgent': request.META.get('HTTP_USER_AGENT', ''),
            },
            ip=None,
            message_id=event_id,          # use same ID in Facebook/Reddit CAPI
            timestamp=timezone.now()
        )

        logger.info("Analytics tracking successful for signup.")
    except Exception as e:
        logger.exception("Analytics tracking failed during signup.")

@receiver(user_logged_in)
def handle_user_logged_in(sender, request, user, **kwargs):
    logger.info(f"User logged in: {user.id} ({user.email})")

    try:
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

        # Note: do not early-return on hard-deleted payloads; we still need to
        # downgrade the user to the free plan when a subscription is deleted.
        stripe.api_key = PaymentsHelper.get_stripe_key()
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

        # Handle explicit deletions (downgrade to free immediately)
        try:
            event_type = getattr(event, "type", "") or getattr(event, "event_type", "")
        except Exception:
            event_type = ""

        span.set_attribute('subscription.event_type', event_type)

        if event_type == "customer.subscription.deleted" or getattr(sub, "status", "") == "canceled":
            downgrade_owner_to_free_plan(owner)

            if owner_type == "user":
                try:
                    Analytics.track_event(
                        user_id=owner.id,
                        event=AnalyticsEvent.SUBSCRIPTION_CANCELLED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'stripe.subscription_id': getattr(sub, 'id', None),
                        },
                    )
                except Exception:
                    logger.exception("Failed to track subscription cancellation for user %s", owner.id)
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
        source_data = stripe_sub if stripe_sub is not None else (getattr(sub, "stripe_data", {}) or {})

        current_period_start_dt = _coerce_datetime(_get_stripe_data_value(source_data, "current_period_start"))
        cancel_at_dt = _coerce_datetime(_get_stripe_data_value(source_data, "cancel_at"))
        cancel_at_period_end_flag = _coerce_bool(_get_stripe_data_value(source_data, "cancel_at_period_end"))

        span.set_attribute('subscription.current_period_start', str(current_period_start_dt))
        span.set_attribute('subscription.cancel_at', str(cancel_at_dt))
        span.set_attribute('subscription.cancel_at_period_end', str(cancel_at_period_end_flag))

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

        # Locate the licensed (base plan) item among subscription items
        licensed_item = None
        try:
            for item in source_data.get("items", {}).get("data", []) or []:
                if item.get("plan", {}).get("usage_type") == "licensed":
                    licensed_item = item
                    break
        except Exception as e:
            logger.warning("Webhook: failed to inspect subscription items for %s: %s", sub.id, e)

        # Proceed only when subscription is active and we found a licensed item
        span.set_attribute('subscription.status', str(sub.status))
        if sub.status == 'active' and licensed_item is not None:
            plan_id = (licensed_item.get("price", {}) or {}).get("product")
            if not plan_id:
                logger.warning("Webhook: missing product on licensed item for subscription %s", sub.id)
                return

            plan = get_plan_by_product_id(plan_id)

            invoice_id = source_data.get("latest_invoice")

            try:
                plan_choice = PlanNamesChoices(plan["id"]) if plan else PlanNamesChoices.FREE
                plan_value = plan_choice.value
            except Exception:
                plan_value = PlanNamesChoices.FREE.value

            if owner_type == "user":
                mark_user_billing_with_plan(owner, plan_value, update_anchor=False)
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

                Analytics.track_event(
                    user_id=owner.id,
                    event=AnalyticsEvent.SUBSCRIPTION_CREATED,
                    source=AnalyticsSource.WEB,
                    properties={
                        'plan': plan_value,
                        'stripe.invoice_id': invoice_id,
                    }
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

                billing = mark_owner_billing_with_plan(owner, plan_value, update_anchor=False)
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
