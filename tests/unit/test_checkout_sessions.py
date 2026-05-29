from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, tag

from billing.checkout_sessions import (
    apply_checkout_tos_consent_collection,
    create_stripe_checkout_session,
)
from constants.feature_flags import STRIPE_CHECKOUT_TOS_CONSENT_REQUIRED


@tag("batch_billing")
class StripeCheckoutSessionConsentTests(SimpleTestCase):
    @patch("billing.checkout_sessions.switch_is_active", return_value=True)
    def test_active_switch_requires_terms_consent_and_preserves_existing_collection(
        self,
        mock_switch_is_active,
    ):
        checkout_kwargs = {"consent_collection": {"promotions": "auto"}}

        apply_checkout_tos_consent_collection(checkout_kwargs)

        mock_switch_is_active.assert_called_once_with(
            STRIPE_CHECKOUT_TOS_CONSENT_REQUIRED,
        )
        self.assertEqual(
            checkout_kwargs["consent_collection"],
            {
                "promotions": "auto",
                "terms_of_service": "required",
            },
        )

    @patch("billing.checkout_sessions.switch_is_active", return_value=False)
    def test_inactive_switch_leaves_consent_collection_unset(
        self,
        _mock_switch_is_active,
    ):
        checkout_kwargs = {"mode": "subscription"}

        apply_checkout_tos_consent_collection(checkout_kwargs)

        self.assertNotIn("consent_collection", checkout_kwargs)

    @patch("billing.checkout_sessions.switch_is_active", return_value=True)
    def test_create_checkout_session_applies_terms_consent(
        self,
        _mock_switch_is_active,
    ):
        stripe_module = SimpleNamespace(
            checkout=SimpleNamespace(
                Session=SimpleNamespace(create=MagicMock(return_value="session"))
            )
        )

        session = create_stripe_checkout_session(
            stripe_module,
            mode="subscription",
            success_url="https://app.test/success",
            cancel_url="https://app.test/cancel",
        )

        self.assertEqual(session, "session")
        stripe_module.checkout.Session.create.assert_called_once_with(
            mode="subscription",
            success_url="https://app.test/success",
            cancel_url="https://app.test/cancel",
            consent_collection={"terms_of_service": "required"},
        )
