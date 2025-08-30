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

from util.payments_helper import PaymentsHelper
from util.subscription_helper import get_user_task_credit_limit, mark_user_billing_with_plan

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
                logger.exception("Failed to parse __utm_first cookie")

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

    # 2. Short-circuit hard-deleted payloads ─ they only contain id, object, customer, deleted
    if payload.get("deleted"):
        return

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

    # Find in stripe_sub the items, and then the one that is usage_type = "licensed". That is the one we care about, as
    # it is the one the base plan - not the add-on tasks.
    if stripe_sub and stripe_sub["items"]["data"][0]["plan"]["usage_type"] == "licensed" and stripe_sub["status"] == "active":
        plan_id = stripe_sub["items"]["data"][0]["price"]["product"]
        plan = get_plan_by_product_id(plan_id)

        # If active subscription, fill the task credits for the user. If the user has no subscription, we don't need to
        # add any credits. Existing credits would expire on their own, but leave them as is as we dont void them on
        # subscription cancellation.
        if sub.status == 'active':
            TaskCreditService.grant_subscription_credits(
                user,
                plan=plan,
                invoice_id=stripe_sub["latest_invoice"]
            )

            try:
                plan_choice = PlanNamesChoices(plan["id"])
                plan_value = plan_choice.value  # This explicitly gets the string value
            except ValueError:
                plan_value = PlanNamesChoices.FREE.value

            mark_user_billing_with_plan(user, plan_value)

            Analytics.identify(user.id, {
                'plan': plan_value,
            })
            Analytics.track_event(
                user_id=user.id,
                event=AnalyticsEvent.SUBSCRIPTION_CREATED,
                source=AnalyticsSource.WEB,
                properties={
                    'plan': plan_value,
                    'stripe.invoice_id': stripe_sub["latest_invoice"]
                }
            )
