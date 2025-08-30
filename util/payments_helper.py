from config import settings

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
        if settings.STRIPE_LIVE_MODE:
            return settings.STRIPE_LIVE_SECRET_KEY

        return settings.STRIPE_TEST_SECRET_KEY