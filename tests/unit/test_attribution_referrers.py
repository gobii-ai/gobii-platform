from types import SimpleNamespace

from django.test import SimpleTestCase, tag

from api.models import UserAttribution
from util.attribution_referrers import (
    first_meaningful_referrer_for_attribution,
    signup_source_bucket_for_attribution,
)


@tag("batch_pages_signals")
class SignupSourceAttributionTests(SimpleTestCase):
    def test_landing_code_has_highest_precedence(self):
        attribution = SimpleNamespace(
            landing_code_first="LP-100",
            gclid_first="gclid-123",
            utm_source_first="youtube",
            first_referrer="https://agentic.ai/",
        )

        self.assertEqual(first_meaningful_referrer_for_attribution(attribution), "landing:LP-100")
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "landing")

    def test_referral_code_beats_raw_referrer(self):
        attribution = SimpleNamespace(
            referrer_code="REF-123",
            first_referrer="https://agentic.ai/",
        )

        self.assertEqual(first_meaningful_referrer_for_attribution(attribution), "referral:REF-123")
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "referral")

    def test_paid_click_id_beats_utm_source(self):
        attribution = SimpleNamespace(
            gclid_first="gclid-123",
            utm_source_first="newsletter",
            utm_medium_first="email",
        )

        self.assertEqual(first_meaningful_referrer_for_attribution(attribution), "google_ads")
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "paid_search")

    def test_social_utm_source_is_bucketed_as_organic_social(self):
        attribution = SimpleNamespace(
            utm_source_first="youtube",
            utm_medium_first="social",
        )

        self.assertEqual(first_meaningful_referrer_for_attribution(attribution), "youtube")
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "organic_social")

    def test_paid_social_utm_source_is_bucketed_as_paid_social(self):
        attribution = SimpleNamespace(
            utm_source_first="linkedin",
            utm_medium_first="paid_social",
        )

        self.assertEqual(first_meaningful_referrer_for_attribution(attribution), "linkedin")
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "paid_social")

    def test_external_referrer_is_used_when_no_stronger_signal_exists(self):
        attribution = SimpleNamespace(first_referrer="https://agentic.ai/blog/")

        self.assertEqual(
            first_meaningful_referrer_for_attribution(attribution),
            "https://agentic.ai/blog/",
        )
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "referral")

    def test_search_referrer_is_bucketed_as_organic_search(self):
        attribution = SimpleNamespace(first_referrer="https://www.google.com/search?q=gobii")

        self.assertEqual(
            first_meaningful_referrer_for_attribution(attribution),
            "https://www.google.com/search?q=gobii",
        )
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "organic_search")

    def test_auth_provider_referrer_is_never_meaningful(self):
        attribution = SimpleNamespace(first_referrer="https://accounts.google.com/")

        self.assertEqual(first_meaningful_referrer_for_attribution(attribution), "direct")
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "direct")

    def test_internal_referrer_is_not_meaningful(self):
        attribution = SimpleNamespace(first_referrer="https://app.gobii.ai/pricing/")

        self.assertEqual(first_meaningful_referrer_for_attribution(attribution), "direct")
        self.assertEqual(signup_source_bucket_for_attribution(attribution), "direct")

    def test_model_properties_expose_derived_source(self):
        attribution = UserAttribution(utm_source_first="google", utm_medium_first="cpc")

        self.assertEqual(attribution.first_meaningful_referrer, "google")
        self.assertEqual(attribution.signup_source_bucket, "paid_search")
