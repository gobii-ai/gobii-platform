from __future__ import annotations

from config.stripe_config import get_stripe_settings
from util.integrations import stripe_status
import logging

logger = logging.getLogger(__name__)


class PaymentsHelper:
    """
    Helper class for payments-related operations.
    """

    @staticmethod
    def get_stripe_key():
        """
        Returns the appropriate Stripe secret key based on the environment. See the environment variables
        STRIPE_LIVE_MODE, STRIPE_LIVE_SECRET_KEY, and STRIPE_TEST_SECRET_KEY.

        Returns:
            str: The Stripe secret key for the current environment.
        """
        status = stripe_status()
        if not status.enabled:
            logger.debug("Stripe key requested while integration disabled: %s", status.reason)
            return None

        stripe = get_stripe_settings()
        if stripe.live_mode:
            logger.info("PaymentsHelper: LIVE mode")
            key = stripe.live_secret_key
        else:
            logger.info("PaymentsHelper: SANDBOX mode")
            key = stripe.test_secret_key

        if not key:
            logger.warning("Stripe requested but secret key missing for %s mode", "live" if stripe.live_mode else "test")
        return key
