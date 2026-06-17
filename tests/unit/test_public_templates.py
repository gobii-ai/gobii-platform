from types import SimpleNamespace
from unittest.mock import patch

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
from pages.public_template_urls import public_template_route_slug


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


class PublicTemplateUrlHelperTests(TestCase):
    @tag("batch_public_templates")
    def test_public_template_route_slug_prefers_slug_then_code(self):
        self.assertEqual(
            public_template_route_slug(SimpleNamespace(slug="custom-template-slug", code="template-code")),
            "custom-template-slug",
        )
        self.assertEqual(
            public_template_route_slug(SimpleNamespace(slug="", code="template-code")),
            "template-code",
        )

    @tag("batch_public_templates")
    def test_public_template_route_slug_returns_empty_string_without_slug_or_code(self):
        self.assertEqual(public_template_route_slug(None), "")
        self.assertEqual(public_template_route_slug(SimpleNamespace(slug=None, code=None)), "")
        self.assertEqual(public_template_route_slug(SimpleNamespace(slug="", code="")), "")


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
    def test_code_backed_curated_template_route_is_disambiguated_by_category(self):
        user = get_user_model().objects.create_user(
            username="public-project-manager-owner",
            email="public-project-manager-owner@example.com",
            password="pw",
        )
        public_profile = PublicProfile.objects.create(user=user, handle="public-pm-owner")
        PersistentAgentTemplate.objects.create(
            code="public-project-manager",
            public_profile=public_profile,
            slug="project-manager",
            display_name="Public Project Manager",
            tagline="A public project manager template.",
            description="A public project manager template.",
            charter="Run the public project manager template.",
            category="Finance",
            is_active=True,
        )
        PersistentAgentTemplate.objects.create(
            code="project-manager",
            public_profile=None,
            slug="",
            display_name="Gobii Project Manager",
            tagline="A curated project manager template.",
            description="A curated project manager template.",
            charter="Run the curated project manager template.",
            category="Team Ops",
            is_active=True,
        )

        curated_response = self.client.get("/library/team-ops/project-manager/")
        public_response = self.client.get("/library/finance/project-manager/")

        self.assertEqual(curated_response.status_code, 200)
        self.assertEqual(curated_response.context["template"].code, "project-manager")
        self.assertContains(curated_response, "Gobii Project Manager")
        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(public_response.context["template"].code, "public-project-manager")
        self.assertContains(public_response, "Public Project Manager")

    @tag("batch_public_templates")
    @patch("pages.views._track_web_event_for_request")
    @patch("pages.views.emit_configured_custom_capi_event")
    @patch("pages.views.Analytics.track_event_anonymous")
    def test_code_backed_curated_template_launch_is_disambiguated_by_category(self, *_mocks):
        user = get_user_model().objects.create_user(
            username="public-project-manager-launch-owner",
            email="public-project-manager-launch-owner@example.com",
            password="pw",
        )
        public_profile = PublicProfile.objects.create(user=user, handle="public-pm-launch-owner")
        PersistentAgentTemplate.objects.create(
            code="public-project-manager",
            public_profile=public_profile,
            slug="project-manager",
            display_name="Public Project Manager",
            tagline="A public project manager template.",
            description="A public project manager template.",
            charter="Run the public project manager template.",
            category="Finance",
            is_active=True,
        )
        PersistentAgentTemplate.objects.create(
            code="project-manager",
            public_profile=None,
            slug="",
            display_name="Gobii Project Manager",
            tagline="A curated project manager template.",
            description="A curated project manager template.",
            charter="Run the curated project manager template.",
            category="Team Ops",
            is_active=True,
        )

        response = self.client.get("/library/team-ops/project-manager/spawn/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY),
            "project-manager",
        )

    @tag("batch_public_templates")
    def test_organization_scoped_template_is_not_publicly_accessible_by_code(self):
        owner = get_user_model().objects.create_user(
            username="private-template-owner",
            email="private-template-owner@example.com",
            password="pw",
        )
        organization = Organization.objects.create(
            name="Private Template Org",
            slug="private-template-org",
            created_by=owner,
        )
        PersistentAgentTemplate.objects.create(
            code="private-project-manager",
            organization=organization,
            public_profile=None,
            slug="",
            display_name="Private Project Manager",
            tagline="Private org-only template.",
            description="Private org-only template.",
            charter="Run the private org-only template.",
            category="Team Ops",
            is_active=True,
        )

        response = self.client.get("/library/team-ops/private-project-manager/")

        self.assertEqual(response.status_code, 404)

    @tag("batch_public_templates")
    def test_organization_scoped_template_is_not_publicly_accessible_by_slug(self):
        owner = get_user_model().objects.create_user(
            username="private-template-slug-owner",
            email="private-template-slug-owner@example.com",
            password="pw",
        )
        organization = Organization.objects.create(
            name="Private Template Slug Org",
            slug="private-template-slug-org",
            created_by=owner,
        )
        PersistentAgentTemplate.objects.create(
            code="private-project-manager-internal",
            organization=organization,
            public_profile=None,
            slug="private-project-manager",
            display_name="Private Project Manager",
            tagline="Private org-only template.",
            description="Private org-only template.",
            charter="Run the private org-only template.",
            category="Team Ops",
            is_active=True,
        )

        response = self.client.get("/library/team-ops/private-project-manager/")

        self.assertEqual(response.status_code, 404)

    @tag("batch_public_templates")
    def test_curated_template_with_custom_slug_resolves_by_slug(self):
        PersistentAgentTemplate.objects.create(
            code="custom-slug-curated-template-code",
            public_profile=None,
            slug="custom-slug-curated-template",
            display_name="Custom Slug Curated Template",
            tagline="A curated template with a custom slug.",
            description="Verifies curated templates can resolve by slug.",
            charter="Use the custom slug for this curated template.",
            category="Team Ops",
            is_active=True,
        )

        response = self.client.get("/library/team-ops/custom-slug-curated-template/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Custom Slug Curated Template")
        self.assertContains(response, "A curated template with a custom slug.")

    @tag("batch_public_templates")
    def test_curated_template_with_custom_slug_redirects_code_path_to_canonical_slug(self):
        PersistentAgentTemplate.objects.create(
            code="custom-slug-curated-template-code",
            public_profile=None,
            slug="custom-slug-curated-template",
            display_name="Custom Slug Curated Template",
            tagline="A curated template with a custom slug.",
            description="Verifies curated templates can resolve by slug.",
            charter="Use the custom slug for this curated template.",
            category="Team Ops",
            is_active=True,
        )

        response = self.client.get("/library/team-ops/custom-slug-curated-template-code/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/library/team-ops/custom-slug-curated-template/")

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
