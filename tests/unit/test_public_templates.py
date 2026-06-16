from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, tag

from agents.services import PretrainedWorkerTemplateService
from api.models import PersistentAgentTemplate, PublicProfile
from api.public_profiles import validate_public_handle
from api.services.template_clone import TemplateCloneService


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
        with self.assertRaises(ValidationError):
            validate_public_handle("library")


class PublicTemplateSlugTests(TestCase):
    @tag("batch_public_templates")
    def test_public_template_slug_is_globally_unique(self):
        user_a = get_user_model().objects.create_user(username="slug-owner-a", email="slug-owner-a@example.com", password="pw")
        user_b = get_user_model().objects.create_user(username="slug-owner-b", email="slug-owner-b@example.com", password="pw")
        profile_a = PublicProfile.objects.create(user=user_a, handle="slug-owner-a")
        profile_b = PublicProfile.objects.create(user=user_b, handle="slug-owner-b")

        PersistentAgentTemplate.objects.create(
            code="global-slug-a",
            public_profile=profile_a,
            slug="global-slug",
            display_name="Global Slug A",
            tagline="First public slug",
            description="First public slug.",
            charter="First public slug.",
            category="Operations",
            is_active=True,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PersistentAgentTemplate.objects.create(
                    code="global-slug-b",
                    public_profile=profile_b,
                    slug="global-slug",
                    display_name="Global Slug B",
                    tagline="Second public slug",
                    description="Second public slug.",
                    charter="Second public slug.",
                    category="Operations",
                    is_active=True,
                )

    @tag("batch_public_templates")
    def test_template_clone_slug_generation_uses_global_public_slugs(self):
        user_a = get_user_model().objects.create_user(username="clone-slug-a", email="clone-slug-a@example.com", password="pw")
        user_b = get_user_model().objects.create_user(username="clone-slug-b", email="clone-slug-b@example.com", password="pw")
        profile_a = PublicProfile.objects.create(user=user_a, handle="clone-slug-a")
        profile_b = PublicProfile.objects.create(user=user_b, handle="clone-slug-b")

        PersistentAgentTemplate.objects.create(
            code="clone-slug-existing",
            public_profile=profile_a,
            slug="ops-brief",
            display_name="Ops Brief",
            tagline="Existing public slug",
            description="Existing public slug.",
            charter="Existing public slug.",
            category="Operations",
            is_active=True,
        )

        slug = TemplateCloneService._generate_template_slug(profile_b, "Ops Brief")

        self.assertEqual(slug, "ops-brief-2")


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
