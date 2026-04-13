from types import SimpleNamespace

from django.contrib import admin
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase, tag
from django.urls import reverse

from pages.admin import CallToActionAdmin
from pages.models import CallToAction, LandingPage


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
        self.factory = RequestFactory()
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

    def test_call_to_action_add_form_renders_new_text_field(self):
        response = self.client.get(reverse("admin:pages_calltoaction_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="new_text"')

    def test_call_to_action_admin_save_model_creates_initial_version(self):
        admin_view = CallToActionAdmin(CallToAction, admin.site)
        request = self.factory.post("/")
        request.user = self.admin_user
        cta = CallToAction(
            slug="cta_homepage_main",
            description="Homepage primary CTA.",
        )
        form = SimpleNamespace(cleaned_data={"new_text": "Start Free Trial"})

        admin_view.save_model(request, cta, form, change=False)

        cta.refresh_from_db()
        self.assertEqual(cta.current_text, "Start Free Trial")
        version = cta.versions.get()
        self.assertEqual(version.created_by, self.admin_user)

    def test_call_to_action_admin_save_model_appends_new_version(self):
        admin_view = CallToActionAdmin(CallToAction, admin.site)
        request = self.factory.post("/")
        request.user = self.admin_user
        cta = CallToAction.objects.create(
            slug="cta_enterprise_request_call_test",
            description="Enterprise request-a-call CTA.",
        )
        cta.add_version("Request a call", created_by=self.admin_user)
        form = SimpleNamespace(cleaned_data={"new_text": "Talk to sales"})

        admin_view.save_model(request, cta, form, change=True)

        cta.refresh_from_db()
        self.assertEqual(cta.current_text, "Talk to sales")
        self.assertEqual(cta.versions.count(), 2)

    def test_call_to_action_change_form_renders_version_history(self):
        cta = CallToAction.objects.create(
            slug="cta_change_history",
            description="CTA with history.",
        )
        cta.add_version("Original text", created_by=self.admin_user)
        cta.add_version("Current text", created_by=self.admin_user)

        response = self.client.get(reverse("admin:pages_calltoaction_change", args=[cta.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CTA version history")
        self.assertContains(response, "Current text")
        self.assertContains(response, "Original text")
