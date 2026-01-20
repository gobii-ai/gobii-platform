from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, tag
from django.urls import reverse

from agents.services import PretrainedWorkerTemplateService
from api.models import PersistentAgentTemplate, PublicProfile
from api.public_profiles import validate_public_handle


class PublicProfileHandleTests(TestCase):
    @tag("batch_public_templates")
    def test_validate_public_handle_normalizes(self):
        self.assertEqual(validate_public_handle(" Bright Compass "), "bright-compass")

    @tag("batch_public_templates")
    def test_validate_public_handle_rejects_reserved(self):
        with self.assertRaises(ValidationError):
            validate_public_handle("console")
        with self.assertRaises(ValidationError):
            validate_public_handle("system")
        with self.assertRaises(ValidationError):
            validate_public_handle("gobii")


class PublicTemplateViewsTests(TestCase):
    @tag("batch_public_templates")
    def test_public_template_detail_renders(self):
        user = get_user_model().objects.create_user(username="owner", email="owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="bright-compass")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-test",
            public_profile=profile,
            slug="ops-brief",
            display_name="Ops Brief",
            tagline="Daily ops snapshot",
            description="Summarizes key operational signals.",
            charter="Summarize ops KPIs and alerts.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Operations",
        )

        response = self.client.get(
            reverse("pages:public_template_detail", kwargs={"handle": profile.handle, "template_slug": template.slug})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, template.display_name)

    @tag("batch_public_templates")
    def test_public_template_hire_sets_session(self):
        user = get_user_model().objects.create_user(username="owner2", email="owner2@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="calm-beacon")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-hire",
            public_profile=profile,
            slug="weekly-digest",
            display_name="Weekly Digest",
            tagline="Weekly ops wrap",
            description="Summarizes weekly ops updates.",
            charter="Compile weekly ops summary.",
            base_schedule="@weekly",
            recommended_contact_channel="email",
            category="Operations",
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse("pages:public_template_hire", kwargs={"handle": profile.handle, "template_slug": template.slug}),
            data={"source_page": "public_template_detail"},
        )
        self.assertEqual(response.status_code, 302)
        session = self.client.session
        self.assertEqual(session.get("agent_charter"), template.charter)
        self.assertEqual(
            session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY),
            template.code,
        )


class TemplateServiceDbTests(TestCase):
    @tag("batch_public_templates")
    def test_get_template_by_code_prefers_db(self):
        template = PersistentAgentTemplate.objects.create(
            code="db-template",
            display_name="DB Template",
            tagline="DB tagline",
            description="DB description",
            charter="DB charter",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Operations",
            is_active=True,
        )

        resolved = PretrainedWorkerTemplateService.get_template_by_code("db-template")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.display_name, template.display_name)
