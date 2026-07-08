import base64
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from agents.services import PretrainedWorkerTemplateService
from api.models import (
    Organization,
    PersistentAgentTemplate,
    PersistentAgentTemplateLike,
    PersistentAgentTemplateRelatedTemplate,
    PersistentAgentTemplateUrlAlias,
    PublicProfile,
)
from api.public_profiles import validate_public_handle
from api.services.template_clone import TemplateCloneService
from pages.library_views import LIBRARY_CACHE_KEY, LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY, LIBRARY_OFFICIAL_CACHE_KEY
from pages.public_template_urls import public_template_route_slug
from tests.utils.llm_seed import get_intelligence_tier


TEST_SOCIAL_IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _test_media_storages(media_root: str, public_media_root: str | None = None) -> dict:
    public_media_root = public_media_root or media_root
    return {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": media_root, "base_url": "/media/"},
        },
        "public_template_social_images": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": public_media_root, "base_url": "/media/"},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


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
            preferred_llm_tier=get_intelligence_tier("premium"),
            category="Operations",
            is_active=True,
        )

        resolved = PretrainedWorkerTemplateService.get_template_by_code("db-template")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.display_name, template.display_name)
        self.assertEqual(resolved.preferred_llm_tier, "premium")


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
    def create_public_template(
        self,
        *,
        code: str,
        display_name: str,
        handle: str,
        slug: str | None = None,
        category: str = "Finance",
        **overrides,
    ) -> PersistentAgentTemplate:
        user = get_user_model().objects.create_user(
            username=f"{code}-owner",
            email=f"{code}-owner@example.com",
            password="pw",
        )
        public_profile = PublicProfile.objects.create(user=user, handle=handle)
        values = {
            "code": code,
            "public_profile": public_profile,
            "slug": slug if slug is not None else code,
            "display_name": display_name,
            "tagline": f"{display_name} tagline.",
            "description": f"{display_name} description.",
            "charter": f"{display_name} charter.",
            "category": category,
            "is_active": True,
        }
        values.update(overrides)
        return PersistentAgentTemplate.objects.create(**values)

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
        self.assertNotContains(response, "Official template")

    @tag("batch_public_templates")
    def test_code_only_curated_template_detail_renders_official_badge_when_checked(self):
        PersistentAgentTemplate.objects.update_or_create(
            code="official-project-manager",
            defaults={
                "public_profile": None,
                "slug": "",
                "display_name": "Official Project Manager",
                "tagline": "Keep projects moving with a reusable project manager.",
                "description": "Tracks project updates and flags blockers.",
                "charter": "Coordinate project updates and flag blockers.",
                "base_schedule": "@daily",
                "recommended_contact_channel": "email",
                "category": "Team Ops",
                "is_official": True,
                "is_active": True,
            },
        )

        response = self.client.get("/library/team-ops/official-project-manager/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Official Project Manager")
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
            preferred_llm_tier=get_intelligence_tier("premium"),
            is_active=True,
        )

        response = self.client.get("/library/team-ops/project-manager/spawn/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY),
            "project-manager",
        )
        self.assertEqual(self.client.session.get("agent_preferred_llm_tier"), "premium")

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
        template = PersistentAgentTemplate.objects.create(
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
        self.assertEqual(response.context["template_seo_title"], f"{template.display_name} AI Agent Template | Gobii")
        self.assertEqual(response.context["template_social_title"], f"{template.display_name} AI Agent Template")

    @tag("batch_public_templates")
    def test_public_template_detail_can_omit_ai_agent_template_from_title(self):
        template = self.create_public_template(
            code="clean-seo-title-template",
            display_name="Clean SEO Title",
            handle="clean-seo-title-template",
            omit_ai_agent_template_title_suffix=True,
        )

        response = self.client.get("/library/finance/clean-seo-title-template/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["template_seo_title"], f"{template.display_name} | Gobii")
        self.assertEqual(response.context["template_social_title"], template.display_name)
        self.assertContains(response, f"<title>{template.display_name} | Gobii</title>", html=True)
        self.assertContains(response, f'<meta property="og:title" content="{template.display_name}">')
        self.assertNotContains(response, f"{template.display_name} AI Agent Template | Gobii")

    @tag("batch_public_templates")
    def test_public_template_detail_hides_related_templates_even_when_specified(self):
        template = self.create_public_template(
            code="hidden-related-source",
            display_name="Hidden Related Source",
            handle="hidden-related-source",
            hide_related_templates=True,
        )
        related_template = self.create_public_template(
            code="hidden-related-target",
            display_name="Hidden Related Target",
            handle="hidden-related-target",
        )
        PersistentAgentTemplateRelatedTemplate.objects.create(
            source_template=template,
            related_template=related_template,
            position=1,
        )

        response = self.client.get("/library/finance/hidden-related-source/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["related_templates"], [])
        self.assertNotContains(response, "Related templates")
        self.assertNotContains(response, "Hidden Related Target")

    @tag("batch_public_templates")
    def test_public_template_detail_uses_specified_related_templates_in_position_order(self):
        template = self.create_public_template(
            code="manual-related-source",
            display_name="Manual Related Source",
            handle="manual-related-source",
        )
        beta_template = self.create_public_template(
            code="manual-related-beta",
            display_name="Beta Related",
            handle="manual-related-beta",
            category="Operations",
        )
        alpha_template = self.create_public_template(
            code="manual-related-alpha",
            display_name="Alpha Related",
            handle="manual-related-alpha",
            category="Research",
        )
        self.create_public_template(
            code="automatic-finance-match",
            display_name="Automatic Finance Match",
            handle="automatic-finance-match",
        )
        PersistentAgentTemplateRelatedTemplate.objects.create(
            source_template=template,
            related_template=beta_template,
            position=20,
        )
        PersistentAgentTemplateRelatedTemplate.objects.create(
            source_template=template,
            related_template=alpha_template,
            position=10,
        )

        response = self.client.get("/library/finance/manual-related-source/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [card["name"] for card in response.context["related_templates"]],
            ["Alpha Related", "Beta Related"],
        )
        self.assertContains(response, "Alpha Related")
        self.assertContains(response, "Beta Related")
        self.assertNotContains(response, "Automatic Finance Match")
        content = response.content.decode("utf-8")
        self.assertLess(content.index("Alpha Related"), content.index("Beta Related"))

    @tag("batch_public_templates")
    def test_curated_template_detail_uses_specified_related_templates(self):
        template = PersistentAgentTemplate.objects.create(
            code="real-estate-research-analyst",
            public_profile=None,
            slug="",
            display_name="Real Estate Research Analyst",
            tagline="Finds properties, pulls comps, and tracks market trends",
            description="Researches comparable properties and market data.",
            charter="Research real estate opportunities.",
            category="Research",
            is_active=True,
        )
        related_template = PersistentAgentTemplate.objects.create(
            code="curated-related-market-monitor",
            public_profile=None,
            slug="",
            display_name="Curated Related Market Monitor",
            tagline="Tracks market signals for related research.",
            description="Tracks market signals.",
            charter="Track market signals.",
            category="Research",
            is_active=True,
        )
        PersistentAgentTemplateRelatedTemplate.objects.create(
            source_template=template,
            related_template=related_template,
            position=1,
        )

        response = self.client.get("/library/research/real-estate-research-analyst/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [card["name"] for card in response.context["related_templates"]],
            ["Curated Related Market Monitor"],
        )
        self.assertContains(response, "Related templates")
        self.assertContains(response, "Curated Related Market Monitor")

    @tag("batch_public_templates")
    def test_related_template_link_rejects_organization_scoped_template(self):
        source_template = PersistentAgentTemplate.objects.create(
            code="public-related-source",
            public_profile=None,
            slug="",
            display_name="Public Related Source",
            tagline="Public source",
            description="Public source.",
            charter="Public source.",
            category="Research",
            is_active=True,
        )
        owner = get_user_model().objects.create_user(
            username="private-related-owner",
            email="private-related-owner@example.com",
            password="pw",
        )
        organization = Organization.objects.create(
            name="Private Related Org",
            slug="private-related-org",
            created_by=owner,
        )
        private_template = PersistentAgentTemplate.objects.create(
            code="private-related-target",
            organization=organization,
            display_name="Private Related Target",
            tagline="Private target",
            description="Private target.",
            charter="Private target.",
            category="Research",
            is_active=True,
        )
        link = PersistentAgentTemplateRelatedTemplate(
            source_template=source_template,
            related_template=private_template,
            position=1,
        )

        with self.assertRaisesMessage(ValidationError, "public-facing"):
            link.full_clean()

    @tag("batch_public_templates")
    def test_related_template_link_rejects_organization_scoped_source_template(self):
        owner = get_user_model().objects.create_user(
            username="private-source-owner",
            email="private-source-owner@example.com",
            password="pw",
        )
        organization = Organization.objects.create(
            name="Private Source Org",
            slug="private-source-org",
            created_by=owner,
        )
        private_source_template = PersistentAgentTemplate.objects.create(
            code="private-related-source",
            organization=organization,
            display_name="Private Related Source",
            tagline="Private source",
            description="Private source.",
            charter="Private source.",
            category="Research",
            is_active=True,
        )
        public_related_template = PersistentAgentTemplate.objects.create(
            code="public-related-target",
            public_profile=None,
            slug="",
            display_name="Public Related Target",
            tagline="Public target",
            description="Public target.",
            charter="Public target.",
            category="Research",
            is_active=True,
        )
        link = PersistentAgentTemplateRelatedTemplate(
            source_template=private_source_template,
            related_template=public_related_template,
            position=1,
        )

        with self.assertRaisesMessage(ValidationError, "organization-scoped"):
            link.full_clean()

    @tag("batch_public_templates")
    def test_public_template_detail_uses_automatic_related_templates_without_specified_links(self):
        self.create_public_template(
            code="automatic-related-source",
            display_name="Automatic Related Source",
            handle="automatic-related-source",
        )
        self.create_public_template(
            code="automatic-related-target",
            display_name="Automatic Related Target",
            handle="automatic-related-target",
        )

        response = self.client.get("/library/finance/automatic-related-source/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Automatic Related Target",
            [card["name"] for card in response.context["related_templates"]],
        )
        self.assertContains(response, "Automatic Related Target")

    @tag("batch_public_templates")
    def test_public_template_detail_hide_tools_suppresses_public_tool_surfaces(self):
        template = self.create_public_template(
            code="hidden-tools-template",
            display_name="Hidden Tools Template",
            handle="hidden-tools-template",
            default_tools=["google_sheets-add-row"],
            expected_tools_summary="Use Sheets to log disputes.",
            hide_tools=True,
        )

        response = self.client.get("/library/finance/hidden-tools-template/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["default_tools"], [])
        self.assertEqual(response.context["template"].default_tools, template.default_tools)
        self.assertFalse(
            any(
                section["key"] == "expected_tools_summary"
                for section in response.context["template_detail_sections"]
            )
        )
        self.assertNotContains(response, "tools enabled")
        self.assertNotContains(response, "Enabled tools")
        self.assertNotContains(response, "Tools it uses")
        self.assertNotContains(response, "Use Sheets to log disputes.")
        self.assertNotContains(response, "Google Sheets Add Row")

    @tag("batch_public_templates")
    def test_public_template_detail_omits_missing_social_image(self):
        user = get_user_model().objects.create_user(
            username="missing-image-template-owner",
            email="missing-image-template-owner@example.com",
            password="pw",
        )
        public_profile = PublicProfile.objects.create(user=user, handle="missing-image-team")
        PersistentAgentTemplate.objects.create(
            code="missing-social-image-template",
            public_profile=public_profile,
            slug="missing-social-image-template",
            display_name="Missing Social Image Template",
            tagline="Render even when static metadata is stale.",
            description="Render even when static metadata is stale.",
            charter="Keep the public page available.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Finance",
            hero_image_path="images/ai-directory/missing.svg",
            is_active=True,
        )

        with patch("pages.views.static", side_effect=ValueError("Missing staticfiles manifest entry")):
            response = self.client.get("/library/finance/missing-social-image-template/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        structured_data = json.loads(response.context["template_structured_data_json"])

        self.assertContains(response, "Missing Social Image Template")
        self.assertNotIn('property="og:image"', content)
        self.assertNotIn('name="twitter:image"', content)
        self.assertNotIn("images/ai-directory/missing.svg", content)
        self.assertNotIn("image", structured_data)

    @tag("batch_public_templates")
    def test_public_template_detail_preserves_absolute_social_image_url(self):
        user = get_user_model().objects.create_user(
            username="absolute-image-template-owner",
            email="absolute-image-template-owner@example.com",
            password="pw",
        )
        public_profile = PublicProfile.objects.create(user=user, handle="absolute-image-team")
        image_url = "https://cdn.example.com/templates/absolute-social-image.png"
        PersistentAgentTemplate.objects.create(
            code="absolute-social-image-template",
            public_profile=public_profile,
            slug="absolute-social-image-template",
            display_name="Absolute Social Image Template",
            tagline="Keep remote social images intact.",
            description="Keep remote social images intact.",
            charter="Use the configured remote image URL.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Finance",
            hero_image_path=image_url,
            is_active=True,
        )

        with patch("pages.views.static", side_effect=AssertionError("static() should not resolve absolute URLs")):
            response = self.client.get("/library/finance/absolute-social-image-template/")

        self.assertEqual(response.status_code, 200)
        structured_data = json.loads(response.context["template_structured_data_json"])

        self.assertContains(response, f'<meta property="og:image" content="{image_url}">')
        self.assertContains(response, f'<meta name="twitter:image" content="{image_url}">')
        self.assertEqual(structured_data["image"], image_url)

    @tag("batch_public_templates")
    def test_public_template_detail_uses_uploaded_social_image(self):
        template = self.create_public_template(
            code="uploaded-social-image-template",
            display_name="Uploaded Social Image Template",
            handle="uploaded-image-team",
            hero_image_path="https://cdn.example.com/templates/fallback-social-image.png",
        )

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(
                MEDIA_ROOT=media_root,
                PUBLIC_SITE_URL="https://gobii.test",
                STORAGES=_test_media_storages(media_root),
            ):
                template.social_image.save("custom-og.png", ContentFile(TEST_SOCIAL_IMAGE_BYTES), save=True)
                try:
                    response = self.client.get("/library/finance/uploaded-social-image-template/")
                    image_url = response.context["template_social_image_url"]
                    image_response = self.client.get(urlsplit(image_url).path)
                finally:
                    template.social_image.delete(save=False)

        self.assertEqual(response.status_code, 200)
        structured_data = json.loads(response.context["template_structured_data_json"])
        self.assertTrue(image_url.startswith("https://gobii.test/library/social-images/public_template_social_images/"))
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.get("Content-Type"), "image/png")
        self.assertEqual(b"".join(image_response.streaming_content), TEST_SOCIAL_IMAGE_BYTES)
        self.assertContains(response, f'<meta property="og:image" content="{image_url}">')
        self.assertContains(response, f'<meta name="twitter:image" content="{image_url}">')
        self.assertNotContains(response, "https://cdn.example.com/templates/fallback-social-image.png")
        self.assertEqual(structured_data["image"], image_url)

    @tag("batch_public_templates")
    def test_public_template_social_image_saves_to_public_storage_alias(self):
        template = self.create_public_template(
            code="public-storage-social-image-template",
            display_name="Public Storage Social Image Template",
            handle="public-storage-image-team",
        )

        with tempfile.TemporaryDirectory() as default_media_root:
            with tempfile.TemporaryDirectory() as public_media_root:
                with override_settings(
                    STORAGES=_test_media_storages(default_media_root, public_media_root),
                ):
                    template.social_image.save("custom-og.png", ContentFile(TEST_SOCIAL_IMAGE_BYTES), save=True)
                    try:
                        saved_name = template.social_image.name
                        self.assertTrue(Path(public_media_root, saved_name).exists())
                        self.assertFalse(Path(default_media_root, saved_name).exists())
                    finally:
                        template.social_image.delete(save=False)

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


class LibraryViewTests(TestCase):
    def setUp(self):
        cache.delete_many([LIBRARY_CACHE_KEY, LIBRARY_OFFICIAL_CACHE_KEY, LIBRARY_CATEGORY_SLUG_MAP_CACHE_KEY])

    def create_public_template(
        self,
        *,
        code: str = "library-public-template",
        slug: str = "library-public-template",
        category: str = "Operations",
        display_name: str = "Library Public Template",
        is_official: bool = False,
        handle: str = "library-owner",
    ):
        user = get_user_model().objects.create_user(
            username=f"{code}-owner",
            email=f"{code}-owner@example.com",
            password="pw",
        )
        profile = PublicProfile.objects.create(user=user, handle=handle)
        return PersistentAgentTemplate.objects.create(
            code=code,
            public_profile=profile,
            slug=slug,
            display_name=display_name,
            tagline=f"{display_name} tagline",
            description=f"{display_name} description.",
            charter=f"{display_name} charter.",
            category=category,
            is_official=is_official,
            is_active=True,
        )

    def create_curated_template(
        self,
        *,
        code: str = "library-curated-template",
        slug: str = "",
        category: str = "Team Ops",
        display_name: str = "Library Curated Template",
        is_official: bool = False,
    ):
        return PersistentAgentTemplate.objects.create(
            code=code,
            public_profile=None,
            slug=slug,
            display_name=display_name,
            tagline=f"{display_name} tagline",
            description=f"{display_name} description.",
            charter=f"{display_name} charter.",
            category=category,
            is_official=is_official,
            is_active=True,
        )

    @tag("batch_public_templates")
    def test_library_index_renders_public_and_curated_templates(self):
        public_template = self.create_public_template()
        curated_template = self.create_curated_template(
            code="gobii-project-manager",
            display_name="Gobii Project Manager",
        )
        PersistentAgentTemplate.objects.create(
            code="library-private-template",
            organization=Organization.objects.create(
                name="Private Library Org",
                slug="private-library-org",
                created_by=get_user_model().objects.create_user(
                    username="private-library-owner",
                    email="private-library-owner@example.com",
                    password="pw",
                ),
            ),
            display_name="Private Library Template",
            tagline="Private",
            description="Private.",
            charter="Private.",
            category="Operations",
            is_active=True,
        )

        response = self.client.get(reverse("pages:library"))

        self.assertEqual(response.status_code, 200)
        payload = response.context["library_initial_payload"]
        self.assertEqual(payload["libraryTotalAgents"], 2)
        self.assertEqual({agent["name"] for agent in payload["agents"]}, {public_template.display_name, curated_template.display_name})
        curated_agent = next(agent for agent in payload["agents"] if agent["id"] == str(curated_template.id))
        self.assertEqual(curated_agent["templateSlug"], curated_template.code)
        self.assertEqual(curated_agent["templateUrl"], "/library/team-ops/gobii-project-manager/")
        self.assertEqual(curated_agent["publicProfileHandle"], "")
        self.assertFalse(curated_agent["isOfficial"])
        self.assertContains(response, "Most popular shared Gobii agents")
        self.assertContains(response, public_template.display_name)
        self.assertContains(response, curated_template.display_name)
        self.assertNotContains(response, "Private Library Template")

    @tag("batch_public_templates")
    def test_library_category_renders_and_redirects_alias_to_canonical_slug(self):
        template = self.create_public_template(
            code="recruiting-template",
            slug="recruiting-template",
            category="HR & Recruiting",
            display_name="Recruiting Template",
        )

        response = self.client.get(reverse("pages:library_category", kwargs={"category_slug": "recruiting"}))
        alias_response = self.client.get("/library/hr-recruiting/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["library_initial_category"], "HR & Recruiting")
        self.assertEqual(response.context["library_initial_payload"]["totalAgents"], 1)
        self.assertContains(response, template.display_name)
        self.assertEqual(alias_response.status_code, 301)
        self.assertEqual(alias_response["Location"], "/library/recruiting/")

    @tag("batch_public_templates")
    def test_libary_typo_redirects_to_library(self):
        response = self.client.get("/libary/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/library/")

    @tag("batch_public_templates")
    def test_library_api_supports_filters_search_and_official_counts(self):
        public_template = self.create_public_template(
            code="budget-beacon",
            slug="budget-beacon",
            category="Finance",
            display_name="Budget Beacon",
            handle="finance-owner",
        )
        official_template = self.create_curated_template(
            code="ops-briefing",
            category="Operations",
            display_name="Ops Briefing",
            is_official=True,
        )
        profileless_template = self.create_curated_template(
            code="vendor-price-watch",
            category="Operations",
            display_name="Vendor Price Watch",
        )
        self.create_public_template(
            code="research-scout",
            slug="research-scout",
            category="Research",
            display_name="Research Scout",
            handle="research-owner",
        )

        response = self.client.get(reverse("pages:library_agents_api"))
        official_response = self.client.get(reverse("pages:library_agents_api"), data={"official": "true"})
        category_response = self.client.get(reverse("pages:library_agents_api"), data={"category": "finance"})
        search_response = self.client.get(reverse("pages:library_agents_api"), data={"q": "finance-owner"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["libraryTotalAgents"], 4)
        self.assertEqual(payload["officialTotalAgents"], 1)
        self.assertEqual(payload["libraryTotalLikes"], 0)
        official_agent = next(agent for agent in payload["agents"] if agent["id"] == str(official_template.id))
        self.assertEqual(official_agent["templateUrl"], "/library/operations/ops-briefing/")
        self.assertTrue(official_agent["isOfficial"])
        profileless_agent = next(agent for agent in payload["agents"] if agent["id"] == str(profileless_template.id))
        self.assertFalse(profileless_agent["isOfficial"])
        self.assertEqual(profileless_agent["publicProfileHandle"], "")

        self.assertEqual(official_response.status_code, 200)
        official_payload = official_response.json()
        self.assertTrue(official_payload["officialOnly"])
        self.assertEqual(official_payload["totalAgents"], 1)
        self.assertEqual([agent["id"] for agent in official_payload["agents"]], [str(official_template.id)])

        self.assertEqual(category_response.status_code, 200)
        category_payload = category_response.json()
        self.assertEqual(category_payload["totalAgents"], 1)
        self.assertEqual([agent["id"] for agent in category_payload["agents"]], [str(public_template.id)])

        self.assertEqual(search_response.status_code, 200)
        search_payload = search_response.json()
        self.assertEqual(search_payload["totalAgents"], 1)
        self.assertEqual([agent["id"] for agent in search_payload["agents"]], [str(public_template.id)])

    @tag("batch_public_templates")
    def test_library_api_supports_pagination(self):
        for index in range(3):
            self.create_public_template(
                code=f"paged-template-{index}",
                slug=f"paged-template-{index}",
                display_name=f"Paged Template {index}",
                handle=f"paged-owner-{index}",
            )

        response = self.client.get(reverse("pages:library_agents_api"), data={"limit": 2, "offset": 1})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["totalAgents"], 3)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(len(payload["agents"]), 2)
        self.assertFalse(payload["hasMore"])

    @tag("batch_public_templates")
    def test_library_like_api_requires_authentication(self):
        template = self.create_public_template()

        response = self.client.post(
            reverse("pages:library_agent_like_api"),
            data=json.dumps({"agentId": str(template.id)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(PersistentAgentTemplateLike.objects.filter(template=template).exists())

    @tag("batch_public_templates")
    def test_library_like_api_toggles_curated_template_like(self):
        template = self.create_curated_template()
        liker = get_user_model().objects.create_user(username="library-liker", email="library-liker@example.com", password="pw")
        self.client.force_login(liker)

        first_response = self.client.post(
            reverse("pages:library_agent_like_api"),
            data=json.dumps({"agentId": str(template.id)}),
            content_type="application/json",
        )
        second_response = self.client.post(
            reverse("pages:library_agent_like_api"),
            data=json.dumps({"agentId": str(template.id)}),
            content_type="application/json",
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertTrue(first_response.json()["isLiked"])
        self.assertEqual(first_response.json()["likeCount"], 1)
        self.assertEqual(second_response.status_code, 200)
        self.assertFalse(second_response.json()["isLiked"])
        self.assertEqual(second_response.json()["likeCount"], 0)
