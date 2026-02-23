from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, tag
from django.urls import reverse

from pages.models import LandingPage


@tag("batch_pages")
class LandingPageAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_user = User.objects.create_superuser(
            username="pages-admin",
            email="pages-admin@example.com",
            password="password123",
        )

    def setUp(self):
        self.client.force_login(self.admin_user)
        Site.objects.update_or_create(
            id=settings.SITE_ID,
            defaults={"domain": "admin.example.test", "name": "admin.example.test"},
        )

    def test_changelist_renders_landing_page_url_column(self):
        landing_page = LandingPage.objects.create(
            code="landing-code",
            charter="Landing page charter",
            title="Landing page title",
        )

        response = self.client.get(reverse("admin:pages_landingpage_changelist"))

        self.assertEqual(response.status_code, 200)
        expected_url = "http://admin.example.test{}".format(
            reverse("pages:landing_redirect", kwargs={"code": landing_page.code})
        )
        self.assertContains(response, expected_url)
