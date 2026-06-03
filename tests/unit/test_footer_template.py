from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings, tag


@tag("oss_readiness_batch")
class FooterTemplateSocialLinkTests(SimpleTestCase):
    @override_settings(
        GOBII_PROPRIETARY_MODE=False,
        PUBLIC_DISCORD_URL="",
        PUBLIC_GITHUB_URL="",
        PUBLIC_LINKEDIN_URL="",
        PUBLIC_MEDIUM_URL="",
        PUBLIC_SUPPORT_EMAIL="",
        PUBLIC_X_URL="",
    )
    def test_footer_omits_configurable_social_links_when_public_urls_are_empty(self):
        html = render_to_string("includes/_footer.html", {"settings": settings})

        self.assertNotIn("linkedin.com/company/gobii-ai", html)
        self.assertNotIn("Follow us on LinkedIn", html)
        self.assertNotIn("medium.com/gobiiai", html)
        self.assertNotIn("Read our Medium blog", html)

    @override_settings(
        GOBII_PROPRIETARY_MODE=False,
        PUBLIC_DISCORD_URL="",
        PUBLIC_GITHUB_URL="",
        PUBLIC_LINKEDIN_URL="https://www.linkedin.com/company/example-ai",
        PUBLIC_MEDIUM_URL="https://medium.com/example-ai",
        PUBLIC_SUPPORT_EMAIL="",
        PUBLIC_X_URL="",
    )
    def test_footer_uses_configured_social_urls(self):
        html = render_to_string("includes/_footer.html", {"settings": settings})

        self.assertIn('href="https://www.linkedin.com/company/example-ai"', html)
        self.assertIn("Follow us on LinkedIn", html)
        self.assertIn('href="https://medium.com/example-ai"', html)
        self.assertIn("Read our Medium blog", html)
