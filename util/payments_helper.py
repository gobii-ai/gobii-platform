from config.stripe_config import get_stripe_settings
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

        Note that dj-stripe requires DB entry for secret, too

        Returns:
            str: The Stripe secret key for the current environment.
        """
        stripe = get_stripe_settings()
        if stripe.live_mode:
            logger.info("PaymentsHelper: LIVE mode")
            return stripe.live_secret_key or ""

        logger.info("PaymentsHelper: SANDBOX mode")
        return stripe.test_secret_key or ""
