from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, tag
from django.urls import reverse

from agents.services import PretrainedWorkerTemplateService
from api.models import (
    Organization,
    PersistentAgentTemplate,
    PersistentAgentTemplateLike,
    PersistentAgentTemplateUrlAlias,
    PublicProfile,
)
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


class PublicTemplateRouteTests(TestCase):
    @tag("batch_public_templates")
    def test_code_only_curated_template_detail_renders_from_library_category_path(self):
        PersistentAgentTemplate.objects.update_or_create(
            code="project-manager",
            defaults={
                "public_profile": None,
                "slug": "",
                "display_name": "Project Manager",
                "tagline": "Keep projects moving with a reusable project manager.",
                "description": "Tracks project updates and flags blockers.",
                "charter": "Coordinate project updates and flag blockers.",
                "base_schedule": "@daily",
                "recommended_contact_channel": "email",
                "category": "Team Ops",
                "is_active": True,
            },
        )

        response = self.client.get("/library/team-ops/project-manager/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Project Manager")
        self.assertContains(response, "Keep projects moving with a reusable project manager.")
        self.assertContains(response, "Official template")

    @tag("batch_public_templates")
    def test_code_only_curated_template_redirects_mismatched_category_to_canonical_path(self):
        PersistentAgentTemplate.objects.update_or_create(
            code="project-manager",
            defaults={
                "public_profile": None,
                "slug": "",
                "display_name": "Project Manager",
                "tagline": "Keep projects moving with a reusable project manager.",
                "description": "Tracks project updates and flags blockers.",
                "charter": "Coordinate project updates and flag blockers.",
                "category": "Team Ops",
                "is_active": True,
            },
        )

        response = self.client.get("/library/ops/project-manager/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/library/team-ops/project-manager/")

    @tag("batch_public_templates")
    def test_public_template_detail_renders_from_library_category_path(self):
        user = get_user_model().objects.create_user(
            username="finance-template-owner",
            email="finance-template-owner@example.com",
            password="pw",
        )
        public_profile = PublicProfile.objects.create(user=user, handle="finance-team")
        PersistentAgentTemplate.objects.create(
            code="stripe-fraud-dispute-monitor",
            public_profile=public_profile,
            slug="stripe-fraud-dispute-monitor",
            display_name="Stripe Fraud Dispute Monitor",
            tagline="Monitor Stripe disputes and flag risky activity.",
            description="Tracks Stripe dispute activity and prepares a review summary.",
            charter="Review new Stripe disputes and summarize suspicious patterns.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Finance",
            is_active=True,
        )

        path = reverse(
            "pages:public_template_detail",
            kwargs={
                "category_slug": "finance",
                "template_slug": "stripe-fraud-dispute-monitor",
            },
        )
        response = self.client.get(path)

        self.assertEqual(path, "/library/finance/stripe-fraud-dispute-monitor/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stripe Fraud Dispute Monitor")
        self.assertContains(response, "Monitor Stripe disputes and flag risky activity.")

    @tag("batch_public_templates")
    def test_public_template_detail_redirects_mismatched_category_to_canonical_path(self):
        user = get_user_model().objects.create_user(
            username="canonical-template-owner",
            email="canonical-template-owner@example.com",
            password="pw",
        )
        public_profile = PublicProfile.objects.create(user=user, handle="canonical-team")
        PersistentAgentTemplate.objects.create(
            code="canonical-finance-template",
            public_profile=public_profile,
            slug="canonical-finance-template",
            display_name="Canonical Finance Template",
            tagline="Keep finance template URLs canonical.",
            description="Keeps finance template URLs canonical.",
            charter="Keep finance template URLs canonical.",
            category="Finance",
            is_active=True,
        )

        response = self.client.get("/library/ops/canonical-finance-template/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/library/finance/canonical-finance-template/")

    @tag("batch_public_templates")
    def test_organization_template_requires_matching_organization(self):
        owner = get_user_model().objects.create_user(username="org-template-owner", email="org-template-owner@example.com", password="pw")
        org = Organization.objects.create(name="Template Org", slug="template-org", created_by=owner)
        other_org = Organization.objects.create(name="Other Template Org", slug="other-template-org", created_by=owner)
        template = PersistentAgentTemplate.objects.create(
            code="org-scoped-template",
            organization=org,
            display_name="Org Scoped Template",
            tagline="Org-only",
            description="Only this organization can use it.",
            charter="Use org context.",
            category="Operations",
            is_active=True,
        )

        self.assertIsNone(PretrainedWorkerTemplateService.get_template_by_code(template.code))
        self.assertIsNone(PretrainedWorkerTemplateService.get_template_by_code(template.code, organization=other_org))

        resolved = PretrainedWorkerTemplateService.get_template_by_code(template.code, organization=org)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.display_name, template.display_name)
        self.assertNotIn(template.code, [item.code for item in PretrainedWorkerTemplateService.get_active_templates()])
