import uuid
import json
from datetime import timedelta, datetime
from django.utils import timezone

from allauth.account.signals import user_signed_up, user_logged_in, user_logged_out
from django.dispatch import receiver

from djstripe.models import Subscription, Customer
from djstripe.event_handlers import djstripe_receiver
from config.plans import get_plan_by_product_id
from constants.plans import PlanNamesChoices
from tasks.services import TaskCreditService

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
import logging
import stripe

from api.models import UserBilling
from util.payments_helper import PaymentsHelper
from util.subscription_helper import get_user_task_credit_limit, mark_user_billing_with_plan, downgrade_user_to_free_plan

logger = logging.getLogger(__name__)

UTM_MAPPING = {
    'source': 'utm_source',
    'medium': 'utm_medium',
    'name': 'utm_campaign',
    'content': 'utm_content',
    'term': 'utm_term'
}

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
    payload = event.data.get("object", {})

    # 1. Ignore anything that isn't a subscription (defensive, though Stripe shouldn't send it)
    if payload.get("object") != "subscription":
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
    if not customer or not customer.subscriber:
        logger.debug("Subscription %s has no linked user; nothing to do.", sub.id)
        return

    user = customer.subscriber

    # Handle explicit deletions (downgrade to free immediately)
    try:
        event_type = getattr(event, "type", "") or getattr(event, "event_type", "")
    except Exception:
        event_type = ""

    if event_type == "customer.subscription.deleted" or getattr(sub, "status", "") == "canceled":
        downgrade_user_to_free_plan(user)
        try:
            Analytics.track_event(
                user_id=user.id,
                event=AnalyticsEvent.SUBSCRIPTION_CANCELLED,
                source=AnalyticsSource.WEB,
                properties={
                    'stripe.subscription_id': getattr(sub, 'id', None),
                },
            )
        except Exception:
            logger.exception("Failed to track subscription cancellation for user %s", user.id)
        return

    # Prefer explicit Stripe retrieve when present; otherwise use dj-stripe's cached payload
    # from the Subscription row. This allows the normal sync_from_stripe_data path to work.
    source_data = stripe_sub if stripe_sub is not None else (getattr(sub, "stripe_data", {}) or {})

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
    if sub.status == 'active' and licensed_item is not None:
        plan_id = (licensed_item.get("price", {}) or {}).get("product")
        if not plan_id:
            logger.warning("Webhook: missing product on licensed item for subscription %s", sub.id)
            return

        plan = get_plan_by_product_id(plan_id)

        # Grant plan credits (idempotent via invoice_id when present)
        invoice_id = source_data.get("latest_invoice")
        TaskCreditService.grant_subscription_credits(
            user,
            plan=plan,
            invoice_id=invoice_id or ""
        )

        try:
            plan_choice = PlanNamesChoices(plan["id"]) if plan else PlanNamesChoices.FREE
            plan_value = plan_choice.value  # explicit string value
        except Exception:
            plan_value = PlanNamesChoices.FREE.value

        # Update the user's billing plan, preserving anchor until we set it from Stripe below
        mark_user_billing_with_plan(user, plan_value, update_anchor=False)

        # Align local anchor day with the Stripe subscription period start for Pro
        try:
            ub = user.billing
            if getattr(sub, 'current_period_start', None):
                new_day = sub.current_period_start.day
                if ub.billing_cycle_anchor != new_day:
                    ub.billing_cycle_anchor = new_day
                    ub.save(update_fields=["billing_cycle_anchor"])
        except UserBilling.DoesNotExist as ue:
            logger.exception("UserBilling record not found for user %s during anchor alignment: %s", user.id, ue)
        except Exception as e:
            logger.exception("Failed to align billing anchor with Stripe period for user %s: %s", user.id, e)

        # Analytics/identify for visibility
        Analytics.identify(user.id, {
            'plan': plan_value,
        })

        Analytics.track_event(
            user_id=user.id,
            event=AnalyticsEvent.SUBSCRIPTION_CREATED,
            source=AnalyticsSource.WEB,
            properties={
                'plan': plan_value,
                'stripe.invoice_id': invoice_id,
            }
        )
