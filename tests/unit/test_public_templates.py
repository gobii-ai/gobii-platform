import json
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from unittest.mock import patch
from waffle.testutils import override_flag

from agents.services import PretrainedWorkerTemplateService
from api.models import (
    PersistentAgentTemplate,
    PersistentAgentTemplateLike,
    PersistentAgentTemplateUrlAlias,
    PublicProfile,
)
from api.public_profiles import validate_public_handle
from api.services.template_clone import TemplateCloneService
from config.redis_client import get_redis_client
from config.socialaccount_adapter import (
    OAUTH_CHARTER_COOKIE,
    OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY,
    build_oauth_charter_stash_cache_key,
)
from pages.library_views import LIBRARY_CACHE_KEY
from pages.public_template_urls import (
    public_template_category_slug,
    public_template_detail_path,
    public_template_hire_path,
    public_template_launch_path,
)
from util.onboarding import (
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
)


def legacy_public_template_detail_path(template):
    return reverse(
        "pages:public_template_legacy_detail",
        kwargs={
            "handle": template.public_profile.handle,
            "template_slug": template.slug,
        },
    )


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

        response = self.client.get(public_template_detail_path(template))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, template.display_name)
        self.assertContains(response, f'href="{reverse("pages:library")}"')
        self.assertContains(response, public_template_hire_path(template))
        self.assertContains(response, f"{template.display_name} AI Agent Template | Gobii")
        self.assertContains(response, '<meta name="description"')
        self.assertContains(response, '<meta property="og:url"')
        self.assertContains(response, '<script type="application/ld+json">')
        self.assertContains(response, '"@type": "SoftwareApplication"')
        self.assertContains(response, 'aria-label="Breadcrumb"')

    @override_settings(
        PUBLIC_SITE_URL="https://www.gobii.ai",
        GOBII_RELEASE_ENV="prod",
        GOBII_PROPRIETARY_MODE=True,
    )
    @tag("batch_public_templates")
    def test_public_template_detail_includes_stable_search_metadata(self):
        user = get_user_model().objects.create_user(username="seo-owner", email="seo-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="seo-owner")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-seo",
            public_profile=profile,
            slug="market-radar",
            display_name="Market Radar",
            tagline="Track market movement",
            description="Tracks public market signals and sends a weekly summary.",
            charter="Track public market signals.",
            base_schedule="@weekly",
            recommended_contact_channel="email",
            category="Research",
        )

        response = self.client.get(public_template_detail_path(template), HTTP_HOST="preview.local")

        detail_url = f"https://www.gobii.ai{public_template_detail_path(template)}"
        soup = BeautifulSoup(response.content, "html.parser")
        self.assertEqual(soup.find("link", rel="canonical")["href"], detail_url)
        self.assertEqual(soup.find("meta", property="og:url")["content"], detail_url)
        self.assertEqual(soup.find("meta", property="og:image")["content"], "https://www.gobii.ai/static/images/gobii_fish_social_1280x640.png")
        self.assertEqual(soup.find("meta", attrs={"name": "twitter:card"})["content"], "summary_large_image")
        self.assertEqual(soup.find("meta", attrs={"name": "twitter:image"})["content"], "https://www.gobii.ai/static/images/gobii_fish_social_1280x640.png")
        self.assertEqual(soup.find("a", string="Research")["href"], "http://preview.local/library/research/")
        self.assertIn("http://preview.local/library/research/market-radar/", soup.get_text(" ", strip=True))

        structured_data = [
            json.loads(script.string)
            for script in soup.find_all("script", {"type": "application/ld+json"})
        ]
        application_schema = next(
            item for item in structured_data if item.get("@type") == "SoftwareApplication"
        )
        self.assertEqual(application_schema["url"], detail_url)
        self.assertEqual(application_schema["image"], "https://www.gobii.ai/static/images/gobii_fish_social_1280x640.png")
        self.assertEqual(application_schema["applicationCategory"], "BusinessApplication")
        self.assertEqual(application_schema["applicationSubCategory"], "Research")
        self.assertEqual(application_schema["operatingSystem"], "Web")

        breadcrumb_schema = next(
            item for item in structured_data if item.get("@type") == "BreadcrumbList"
        )
        self.assertEqual(breadcrumb_schema["itemListElement"][-1]["item"], detail_url)

    @override_settings(PUBLIC_SITE_URL="https://www.gobii.ai")
    @tag("batch_public_templates")
    def test_public_template_detail_escapes_json_ld_script_closing_sequence(self):
        display_name = 'Bad </script><script>alert("x")</script>'
        description = "Description </script><img src=x onerror=alert(1)>"
        user = get_user_model().objects.create_user(username="unsafe-owner", email="unsafe-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="unsafe-owner")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-unsafe",
            public_profile=profile,
            slug="unsafe-template",
            display_name=display_name,
            tagline="Unsafe tagline",
            description=description,
            charter="Do useful work.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Operations",
        )

        response = self.client.get(public_template_detail_path(template))

        content = response.content.decode()
        self.assertIn("\\u003C/script\\u003E\\u003Cscript\\u003Ealert", content)
        self.assertNotIn('</script><script>alert("x")</script>', content)

        soup = BeautifulSoup(response.content, "html.parser")
        structured_data = [
            json.loads(script.string)
            for script in soup.find_all("script", {"type": "application/ld+json"})
        ]
        application_schema = next(
            item for item in structured_data if item.get("@type") == "SoftwareApplication"
        )
        self.assertEqual(application_schema["name"], display_name)
        self.assertEqual(application_schema["description"], description)
        breadcrumb_schema = next(
            item for item in structured_data if item.get("@type") == "BreadcrumbList"
        )
        self.assertEqual(breadcrumb_schema["itemListElement"][-1]["name"], display_name)

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    @tag("batch_public_templates")
    def test_public_template_detail_omits_trial_onboarding_fields_in_community_mode(self):
        user = get_user_model().objects.create_user(username="owner-community", email="owner-community@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="quiet-forest")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-community",
            public_profile=profile,
            slug="ops-radar",
            display_name="Ops Radar",
            tagline="Watch operations signals",
            description="Tracks operational changes.",
            charter="Track operational changes.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Operations",
        )

        response = self.client.get(public_template_detail_path(template))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="trial_onboarding" value="1"')
        self.assertNotContains(
            response,
            f'name="trial_onboarding_target" value="{TRIAL_ONBOARDING_TARGET_AGENT_UI}"',
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @tag("batch_public_templates")
    def test_public_template_detail_includes_trial_onboarding_fields_in_proprietary_mode(self):
        user = get_user_model().objects.create_user(username="owner-pro", email="owner-pro@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="bright-ridge")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-pro",
            public_profile=profile,
            slug="ops-signal",
            display_name="Ops Signal",
            tagline="Signal operational changes",
            description="Highlights operational changes.",
            charter="Highlight operational changes.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Operations",
        )

        response = self.client.get(public_template_detail_path(template))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="trial_onboarding" value="1"')
        self.assertContains(
            response,
            f'name="trial_onboarding_target" value="{TRIAL_ONBOARDING_TARGET_AGENT_UI}"',
        )

    @tag("batch_public_templates")
    def test_legacy_public_template_detail_redirects_permanently_to_canonical_library_url(self):
        user = get_user_model().objects.create_user(username="owner-legacy", email="owner-legacy@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="quiet-harbor")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-legacy",
            public_profile=profile,
            slug="b2b-sales-lead-generator",
            display_name="B2B Sales Lead Generator",
            tagline="Find B2B leads",
            description="Finds B2B sales leads.",
            charter="Find B2B sales leads.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Sales",
        )

        response = self.client.get(legacy_public_template_detail_path(template))

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, "/library/sales/b2b-sales-lead-generator/")

    @tag("batch_public_templates")
    def test_legacy_public_template_alias_redirects_after_slug_collision_rename(self):
        user = get_user_model().objects.create_user(username="owner-alias", email="owner-alias@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="lucid-voyage")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-alias",
            public_profile=profile,
            slug="renewable-energy-market-analyst-2",
            display_name="Renewable Energy Market Analyst",
            tagline="Track renewable energy markets",
            description="Tracks renewable energy markets.",
            charter="Track renewable energy markets.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Research",
        )
        PersistentAgentTemplateUrlAlias.objects.create(
            template=template,
            public_profile=profile,
            slug="renewable-energy-market-analyst",
        )

        response = self.client.get("/lucid-voyage/renewable-energy-market-analyst/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, public_template_detail_path(template))

    @tag("batch_public_templates")
    def test_public_template_detail_recovers_from_stale_category_slug(self):
        user = get_user_model().objects.create_user(username="owner-recovery", email="owner-recovery@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="steady-signal")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-recovery",
            public_profile=profile,
            slug="market-research-agent",
            display_name="Market Research Agent",
            tagline="Research markets",
            description="Researches markets.",
            charter="Research markets.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Research",
        )

        response = self.client.get("/library/old-category/market-research-agent/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, public_template_detail_path(template))

    @tag("batch_public_templates")
    def test_public_template_launch_recovers_from_stale_category_slug(self):
        user = get_user_model().objects.create_user(username="owner-launch-recovery", email="owner-launch-recovery@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="steady-launch")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-launch-recovery",
            public_profile=profile,
            slug="market-launch-agent",
            display_name="Market Launch Agent",
            tagline="Research markets",
            description="Researches markets.",
            charter="Research markets.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Research",
        )

        response = self.client.get("/library/old-category/market-launch-agent/spawn/?utm_source=share")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, f"{public_template_launch_path(template)}?utm_source=share")

    @tag("batch_public_templates")
    def test_public_template_launch_redirects_authenticated_user_into_app_spawn(self):
        user = get_user_model().objects.create_user(username="launch-user", email="launch-user@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="launch-profile")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-launch-auth",
            public_profile=profile,
            slug="weekly-launch-digest",
            display_name="Weekly Launch Digest",
            tagline="Weekly ops wrap",
            description="Summarizes weekly ops updates.",
            charter="Compile weekly ops summary.",
            base_schedule="@weekly",
            recommended_contact_channel="email",
            category="Operations",
        )
        self.client.force_login(user)
        session = self.client.session
        session["agent_charter"] = "Old draft"
        session["referrer_code"] = "old-referrer"
        session.save()

        with (
            patch("pages.views.Analytics.track_event"),
            patch("pages.views.emit_configured_custom_capi_event"),
        ):
            response = self.client.get(
                public_template_launch_path(template),
                {"utm_source": "newsletter", "return_to": public_template_detail_path(template)},
            )

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, "/app/agents/new")
        params = parse_qs(parsed.query)
        self.assertEqual(params.get("spawn"), ["1"])
        self.assertEqual(params.get("utm_source"), ["newsletter"])
        self.assertEqual(params.get("return_to"), [public_template_detail_path(template)])

        session = self.client.session
        self.assertEqual(session.get("agent_charter"), template.charter)
        self.assertEqual(session.get("agent_charter_source"), "template")
        self.assertEqual(
            session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY),
            template.code,
        )
        self.assertEqual(session.get("signup_template_code"), template.code)
        self.assertNotIn("referrer_code", session)

    @tag("batch_public_templates")
    def test_public_template_launch_redirects_anon_to_login_and_stashes_template(self):
        user = get_user_model().objects.create_user(username="launch-anon-owner", email="launch-anon-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="launch-anon")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-launch-anon",
            public_profile=profile,
            slug="sales-launch-desk",
            display_name="Sales Launch Desk",
            tagline="Qualify inbound leads",
            description="Screens leads and drafts follow-ups.",
            charter="Qualify leads and draft next steps.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Sales",
        )
        session = self.client.session
        session["utm_querystring"] = "utm_medium=ads"
        session.save()

        with (
            patch("pages.views.Analytics.track_event_anonymous"),
            patch("pages.views.emit_configured_custom_capi_event"),
        ):
            response = self.client.get(
                public_template_launch_path(template),
                {"utm_source": "shared-link"},
            )

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_login"))
        params = parse_qs(parsed.query)
        next_url = params.get("next")[0]
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        next_params = parse_qs(next_parts.query)
        self.assertEqual(next_params.get("spawn"), ["1"])
        self.assertEqual(next_params.get("utm_source"), ["shared-link"])

        self.assertIn(OAUTH_CHARTER_COOKIE, response.cookies)
        stash_token_payload = signing.loads(response.cookies[OAUTH_CHARTER_COOKIE].value, max_age=7200)
        stash_token = stash_token_payload.get(OAUTH_CHARTER_SERVER_SIDE_TOKEN_KEY)
        self.assertIsNotNone(stash_token)
        cached_charter_payload = signing.loads(
            get_redis_client().get(build_oauth_charter_stash_cache_key(stash_token))
        )
        self.assertEqual(cached_charter_payload.get("agent_charter"), template.charter)
        self.assertEqual(cached_charter_payload.get("agent_charter_source"), "template")
        self.assertEqual(
            cached_charter_payload.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY),
            template.code,
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("pages.views.can_user_use_personal_agents_and_api", return_value=False)
    @tag("batch_public_templates")
    def test_public_template_launch_marks_required_trial_selection_for_blocked_authenticated_user(self, _mock_can_use):
        user = get_user_model().objects.create_user(username="launch-trial-user", email="launch-trial-user@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="launch-trial")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-launch-trial",
            public_profile=profile,
            slug="trial-launch-desk",
            display_name="Trial Launch Desk",
            tagline="Qualify inbound leads",
            description="Screens leads and drafts follow-ups.",
            charter="Qualify leads and draft next steps.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Sales",
        )
        self.client.force_login(user)

        with (
            patch("pages.views.Analytics.track_event"),
            patch("pages.views.emit_configured_custom_capi_event"),
        ):
            response = self.client.get(public_template_launch_path(template))

        self.assertEqual(response.status_code, 302)
        session = self.client.session
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        self.assertTrue(session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY))

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
            public_template_hire_path(template),
            data={"source_page": "public_template_detail"},
        )
        self.assertEqual(response.status_code, 302)
        session = self.client.session
        self.assertEqual(session.get("agent_charter"), template.charter)
        self.assertEqual(
            session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY),
            template.code,
        )

    @tag("batch_public_templates")
    @patch("pages.views.emit_configured_custom_capi_event")
    def test_public_template_hire_emits_template_launched_custom_event(self, mock_emit_custom_event):
        user = get_user_model().objects.create_user(username="owner2b", email="owner2b@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="calm-beacon-2")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-hire-capi",
            public_profile=profile,
            slug="weekly-digest-capi",
            display_name="Weekly Digest",
            tagline="Weekly ops wrap",
            description="Summarizes weekly ops updates.",
            charter="Compile weekly ops summary.",
            base_schedule="@weekly",
            recommended_contact_channel="email",
            category="Operations",
        )

        response = self.client.post(
            public_template_hire_path(template),
            data={"source_page": "public_template_detail", "flow": "pro"},
        )

        self.assertEqual(response.status_code, 302)
        mock_emit_custom_event.assert_called_once()
        call_kwargs = mock_emit_custom_event.call_args.kwargs
        self.assertIsNone(call_kwargs["user"])
        self.assertIsNone(call_kwargs["plan_owner"])
        self.assertEqual(call_kwargs["event_name"], "TemplateLaunched")
        self.assertEqual(call_kwargs["properties"]["template_id"], str(template.id))
        self.assertEqual(call_kwargs["properties"]["template_code"], template.code)
        self.assertEqual(call_kwargs["properties"]["source_page"], "public_template_detail")
        self.assertEqual(call_kwargs["properties"]["flow"], "pro")

    @tag("batch_public_templates")
    def test_public_template_hire_sets_trial_onboarding_for_anonymous_user(self):
        user = get_user_model().objects.create_user(username="owner3", email="owner3@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="steady-harbor")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-trial",
            public_profile=profile,
            slug="sales-desk",
            display_name="Sales Desk",
            tagline="Qualify inbound leads",
            description="Screens leads and drafts follow-ups.",
            charter="Qualify leads and draft next steps.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Sales",
        )

        response = self.client.post(
            public_template_hire_path(template),
            data={
                "source_page": "public_template_detail",
                "trial_onboarding": "1",
                "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_AGENT_UI,
            },
        )

        self.assertEqual(response.status_code, 302)
        session = self.client.session
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )

    @tag("batch_public_templates")
    def test_public_template_hire_redirects_to_signup_when_cta_signup_first_enabled(self):
        user = get_user_model().objects.create_user(username="owner4", email="owner4@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="harbor-signal")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-signup-first",
            public_profile=profile,
            slug="sales-desk-signup",
            display_name="Sales Desk",
            tagline="Qualify inbound leads",
            description="Screens leads and drafts follow-ups.",
            charter="Qualify leads and draft next steps.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Sales",
        )

        session = self.client.session
        session["utm_querystring"] = "utm_source=template-directory"
        session.save()

        with override_flag("cta_signup_first", active=True):
            response = self.client.post(
                public_template_hire_path(template),
                data={
                    "source_page": "public_template_detail",
                    "trial_onboarding": "1",
                    "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_AGENT_UI,
                },
            )

        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("account_signup"))

        params = parse_qs(parsed.query)
        next_url = params.get("next")[0]
        next_parts = urlparse(next_url)
        self.assertEqual(next_parts.path, "/app/agents/new")
        next_params = parse_qs(next_parts.query)
        self.assertEqual(next_params.get("spawn"), ["1"])
        self.assertEqual(params.get("utm_source"), ["template-directory"])

        session = self.client.session
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )

    @tag("batch_public_templates")
    def test_public_template_hire_modal_prep_returns_modal_signup_url_and_preserves_state(self):
        user = get_user_model().objects.create_user(username="owner4b", email="owner4b@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="harbor-signal-modal")
        template = PersistentAgentTemplate.objects.create(
            code="tpl-signup-modal",
            public_profile=profile,
            slug="sales-desk-modal",
            display_name="Sales Desk",
            tagline="Qualify inbound leads",
            description="Screens leads and drafts follow-ups.",
            charter="Qualify leads and draft next steps.",
            base_schedule="@daily",
            recommended_contact_channel="email",
            category="Sales",
        )

        with override_flag("cta_signup_modal", active=True):
            response = self.client.post(
                public_template_hire_path(template),
                data={
                    "source_page": "public_template_detail",
                    "trial_onboarding": "1",
                    "trial_onboarding_target": TRIAL_ONBOARDING_TARGET_AGENT_UI,
                    "auth_modal": "1",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        parsed = urlparse(payload["auth_url"])
        self.assertEqual(parsed.path, reverse("account_signup_modal"))
        next_url = parse_qs(parsed.query).get("next", [None])[0]
        self.assertIsNotNone(next_url)
        self.assertEqual(urlparse(next_url).path, "/app/agents/new")

        session = self.client.session
        self.assertEqual(session.get("agent_charter"), template.charter)
        self.assertEqual(
            session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY),
            template.code,
        )
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))


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


class LibraryViewsTests(TestCase):
    def setUp(self):
        cache.delete(LIBRARY_CACHE_KEY)

    @tag("batch_public_templates")
    def test_library_page_renders_react_mount(self):
        response = self.client.get(reverse("pages:library"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="gobii-frontend-root"')
        self.assertContains(response, 'data-app="library"')
        self.assertContains(response, 'data-props-json-id="library-initial-payload"')
        self.assertContains(response, 'id="library-initial-payload"')
        self.assertContains(response, '<meta name="description"')
        self.assertContains(response, '<meta property="og:url"')
        self.assertContains(response, '<script type="application/ld+json">')
        self.assertContains(response, '"@type": "CollectionPage"')
        self.assertNotContains(response, "Loading shared agents")

    @tag("batch_public_templates")
    def test_library_page_renders_initial_public_templates_for_crawlers(self):
        user = get_user_model().objects.create_user(username="library-ssr-owner", email="library-ssr-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="library-ssr-owner")
        template = PersistentAgentTemplate.objects.create(
            code="lib-ssr-ops",
            public_profile=profile,
            slug="ops-automator",
            display_name="SSR Ops Automator",
            tagline="Automate operations checks",
            description="Tracks recurring operations work.",
            charter="Automate operations checks.",
            category="Operations",
            is_active=True,
        )

        response = self.client.get(reverse("pages:library"))
        self.assertEqual(response.status_code, 200)
        detail_url = public_template_detail_path(template)
        self.assertContains(response, "SSR Ops Automator")
        self.assertContains(response, "Automate operations checks")
        self.assertContains(response, f'href="{detail_url}"')
        self.assertContains(response, "Operations")
        self.assertContains(response, '"@type": "ItemList"')
        self.assertContains(response, '"agents":')

    @tag("batch_public_templates")
    def test_library_category_page_renders_filtered_initial_payload(self):
        user = get_user_model().objects.create_user(username="library-category-owner", email="library-category-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="library-category-owner")
        sales_template = PersistentAgentTemplate.objects.create(
            code="lib-category-sales",
            public_profile=profile,
            slug="sales-agent",
            display_name="Sales Agent",
            tagline="Sales work",
            description="Handles sales work.",
            charter="Handle sales work.",
            category="Sales",
            is_active=True,
        )
        PersistentAgentTemplate.objects.create(
            code="lib-category-research",
            public_profile=profile,
            slug="research-agent",
            display_name="Research Agent",
            tagline="Research work",
            description="Handles research work.",
            charter="Handle research work.",
            category="Research",
            is_active=True,
        )

        response = self.client.get(reverse("pages:library_category", kwargs={"category_slug": "sales"}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sales AI Agent Templates | Gobii")
        self.assertContains(response, 'data-library-initial-category="Sales"')
        self.assertContains(response, sales_template.display_name)
        self.assertNotContains(response, "Research Agent")

    @tag("batch_public_templates")
    def test_library_recruiting_category_preserves_indexed_slug_for_hr_recruiting(self):
        user = get_user_model().objects.create_user(username="library-recruiting-owner", email="library-recruiting-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="library-recruiting-owner")
        recruiting_template = PersistentAgentTemplate.objects.create(
            code="lib-category-recruiting",
            public_profile=profile,
            slug="candidate-sourcer",
            display_name="Candidate Sourcer",
            tagline="Recruiting work",
            description="Sources recruiting candidates.",
            charter="Source recruiting candidates.",
            category="HR & Recruiting",
            is_active=True,
        )
        PersistentAgentTemplate.objects.create(
            code="lib-category-ops-for-recruiting-test",
            public_profile=profile,
            slug="ops-agent",
            display_name="Ops Agent",
            tagline="Ops work",
            description="Handles ops work.",
            charter="Handle ops work.",
            category="Operations",
            is_active=True,
        )
        cache.clear()

        response = self.client.get("/library/recruiting/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        self.assertIn("HR & Recruiting AI Agent Templates | Gobii", soup.find("title").get_text())
        app_mount = soup.select_one("[data-library-initial-category]")
        self.assertIsNotNone(app_mount)
        self.assertEqual(app_mount["data-library-initial-category"], "HR & Recruiting")
        self.assertContains(response, recruiting_template.display_name)
        self.assertNotContains(response, "Ops Agent")
        self.assertEqual(public_template_category_slug(recruiting_template), "recruiting")
        self.assertEqual(public_template_detail_path(recruiting_template), "/library/recruiting/candidate-sourcer/")

        alias_response = self.client.get("/library/hr-recruiting/")

        self.assertEqual(alias_response.status_code, 301)
        self.assertEqual(alias_response.url, "/library/recruiting/")

        mixed_case_response = self.client.get("/library/Recruiting/")

        self.assertEqual(mixed_case_response.status_code, 301)
        self.assertEqual(mixed_case_response.url, "/library/recruiting/")

        sitemap_response = self.client.get("/sitemap.xml")

        self.assertContains(sitemap_response, "http://example.com/library/recruiting/")
        self.assertNotContains(sitemap_response, "http://example.com/library/hr-recruiting/")

    @tag("batch_public_templates")
    def test_library_category_route_preserves_legacy_library_handle_urls(self):
        user = get_user_model().objects.create_user(username="legacy-library-owner", email="legacy-library-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="library")
        template = PersistentAgentTemplate.objects.create(
            code="legacy-library-template",
            public_profile=profile,
            slug="ops-agent",
            display_name="Ops Agent",
            tagline="Ops work",
            description="Handles ops work.",
            charter="Handle ops work.",
            category="Operations",
            is_active=True,
        )

        response = self.client.get("/library/ops-agent/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, public_template_detail_path(template))

    @tag("batch_public_templates")
    def test_library_category_route_prefers_legacy_library_template_when_slug_collides_with_category(self):
        user = get_user_model().objects.create_user(username="legacy-library-sales-owner", email="legacy-library-sales-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="library")
        template = PersistentAgentTemplate.objects.create(
            code="legacy-library-sales-template",
            public_profile=profile,
            slug="sales",
            display_name="Sales Agent",
            tagline="Sales work",
            description="Handles sales work.",
            charter="Handle sales work.",
            category="Sales",
            is_active=True,
        )

        response = self.client.get("/library/sales/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, public_template_detail_path(template))

    @tag("batch_public_templates")
    def test_libary_path_redirects_to_library(self):
        response = self.client.get("/libary/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse("pages:library"))

    @tag("batch_public_templates")
    def test_sitemap_includes_library_and_public_template_urls(self):
        user = get_user_model().objects.create_user(username="library-sitemap-owner", email="library-sitemap-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="library-sitemap-owner")
        template = PersistentAgentTemplate.objects.create(
            code="lib-sitemap-template",
            public_profile=profile,
            slug="sitemap-template",
            display_name="Sitemap Template",
            tagline="Sitemap coverage",
            description="Ensures sitemap coverage.",
            charter="Ensure sitemap coverage.",
            category="Operations",
            is_active=True,
        )

        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("http://example.com/library/", content)
        self.assertIn("http://example.com/library/operations/", content)
        self.assertIn(
            f"http://example.com{public_template_detail_path(template)}",
            content,
        )

    @tag("batch_public_templates")
    def test_library_api_returns_public_active_templates(self):
        user = get_user_model().objects.create_user(username="library-owner", email="library-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="library-owner")

        operations_agent = PersistentAgentTemplate.objects.create(
            code="lib-ops-1",
            public_profile=profile,
            slug="ops-automator",
            display_name="Ops Automator",
            tagline="Automate operations checks",
            description="Tracks recurring operations work.",
            charter="Automate operations checks.",
            category="Operations",
            is_official=True,
            is_active=True,
        )
        PersistentAgentTemplate.objects.create(
            code="lib-ops-2",
            public_profile=profile,
            slug="ops-watcher",
            display_name="Ops Watcher",
            tagline="Operations watchtower",
            description="Monitors critical operations events.",
            charter="Monitor operations events.",
            category="Operations",
            is_active=True,
        )
        PersistentAgentTemplate.objects.create(
            code="lib-research-1",
            public_profile=profile,
            slug="research-scout",
            display_name="Research Scout",
            tagline="Research signals quickly",
            description="Collects and summarizes findings.",
            charter="Gather findings and summarize.",
            category="Research",
            is_active=True,
        )
        # Inactive public template should be excluded.
        PersistentAgentTemplate.objects.create(
            code="lib-inactive",
            public_profile=profile,
            slug="inactive-agent",
            display_name="Inactive Agent",
            tagline="Should not list",
            description="Inactive template.",
            charter="Inactive.",
            category="Operations",
            is_active=False,
        )
        # Non-public template should be excluded.
        PersistentAgentTemplate.objects.create(
            code="lib-private",
            display_name="Private Agent",
            tagline="Should not list",
            description="Private template.",
            charter="Private.",
            category="Research",
            is_active=True,
        )

        response = self.client.get(reverse("pages:library_agents_api"))
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload["totalAgents"], 3)
        self.assertEqual(payload["libraryTotalAgents"], 3)
        self.assertEqual(len(payload["agents"]), 3)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["limit"], 24)
        self.assertFalse(payload["hasMore"])
        self.assertEqual(payload["topCategories"][0], {"name": "Operations", "count": 2})
        self.assertEqual(payload["topCategories"][1], {"name": "Research", "count": 1})

        first_agent = next(agent for agent in payload["agents"] if agent["id"] == str(operations_agent.id))
        self.assertEqual(first_agent["publicProfileHandle"], "library-owner")
        self.assertEqual(
            first_agent["templateUrl"],
            public_template_detail_path(operations_agent),
        )
        self.assertEqual(first_agent["categorySlug"], "operations")
        self.assertTrue(first_agent["isOfficial"])
        self.assertEqual(first_agent["likeCount"], 0)
        self.assertFalse(first_agent["isLiked"])

    @tag("batch_public_templates")
    def test_library_api_supports_pagination_and_category_filter(self):
        user = get_user_model().objects.create_user(username="library-owner-2", email="library-owner-2@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="library-owner-2")

        for index in range(3):
            PersistentAgentTemplate.objects.create(
                code=f"lib-ops-page-{index}",
                public_profile=profile,
                slug=f"ops-page-{index}",
                display_name=f"Ops Page {index}",
                tagline="Ops pagination",
                description="Operations pagination item.",
                charter="Operations pagination item.",
                category="Operations",
                is_active=True,
            )

        for index in range(2):
            PersistentAgentTemplate.objects.create(
                code=f"lib-research-page-{index}",
                public_profile=profile,
                slug=f"research-page-{index}",
                display_name=f"Research Page {index}",
                tagline="Research pagination",
                description="Research pagination item.",
                charter="Research pagination item.",
                category="Research",
                is_active=True,
            )

        paged_response = self.client.get(reverse("pages:library_agents_api"), data={"limit": 2, "offset": 1})
        self.assertEqual(paged_response.status_code, 200)
        paged_payload = paged_response.json()
        self.assertEqual(paged_payload["totalAgents"], 5)
        self.assertEqual(paged_payload["libraryTotalAgents"], 5)
        self.assertEqual(paged_payload["offset"], 1)
        self.assertEqual(paged_payload["limit"], 2)
        self.assertEqual(len(paged_payload["agents"]), 2)
        self.assertTrue(paged_payload["hasMore"])

        filtered_response = self.client.get(reverse("pages:library_agents_api"), data={"category": "research", "limit": 1, "offset": 0})
        self.assertEqual(filtered_response.status_code, 200)
        filtered_payload = filtered_response.json()
        self.assertEqual(filtered_payload["totalAgents"], 2)
        self.assertEqual(filtered_payload["libraryTotalAgents"], 5)
        self.assertEqual(filtered_payload["offset"], 0)
        self.assertEqual(filtered_payload["limit"], 1)
        self.assertEqual(len(filtered_payload["agents"]), 1)
        self.assertTrue(filtered_payload["hasMore"])
        self.assertEqual(filtered_payload["agents"][0]["category"], "Research")

    @tag("batch_public_templates")
    def test_library_api_supports_search_across_fields(self):
        user = get_user_model().objects.create_user(username="library-search-owner", email="library-search-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=user, handle="search-owner")

        name_match = PersistentAgentTemplate.objects.create(
            code="lib-search-name",
            public_profile=profile,
            slug="budget-beacon",
            display_name="Budget Beacon",
            tagline="Cost insights",
            description="Tracks weekly spending posture.",
            charter="Track weekly spending posture.",
            category="Operations",
            is_active=True,
        )
        tagline_match = PersistentAgentTemplate.objects.create(
            code="lib-search-tagline",
            public_profile=profile,
            slug="release-sentinel",
            display_name="Release Sentinel",
            tagline="Compliance signal monitor",
            description="Monitors release readiness.",
            charter="Monitor release readiness.",
            category="Operations",
            is_active=True,
        )
        description_match = PersistentAgentTemplate.objects.create(
            code="lib-search-description",
            public_profile=profile,
            slug="market-watch",
            display_name="Market Watch",
            tagline="Trend alerts",
            description="Tracks competitor positioning and activity.",
            charter="Track competitor positioning and activity.",
            category="Research",
            is_active=True,
        )
        category_match = PersistentAgentTemplate.objects.create(
            code="lib-search-category",
            public_profile=profile,
            slug="invoice-tracker",
            display_name="Invoice Tracker",
            tagline="Payment controls",
            description="Keeps invoice workflows moving.",
            charter="Track invoice workflows.",
            category="Finance",
            is_active=True,
        )
        PersistentAgentTemplate.objects.create(
            code="lib-search-extra",
            public_profile=profile,
            slug="ops-scheduler",
            display_name="Ops Scheduler",
            tagline="Scheduling assistant",
            description="Coordinates scheduled jobs.",
            charter="Coordinate scheduled jobs.",
            category="Operations",
            is_active=True,
        )

        def fetch_ids(query: str) -> tuple[int, set[str]]:
            response = self.client.get(reverse("pages:library_agents_api"), data={"q": query})
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            return payload["totalAgents"], {agent["id"] for agent in payload["agents"]}

        total_agents, agent_ids = fetch_ids("budget")
        self.assertEqual(total_agents, 1)
        self.assertEqual(agent_ids, {str(name_match.id)})

        total_agents, agent_ids = fetch_ids("compliance")
        self.assertEqual(total_agents, 1)
        self.assertEqual(agent_ids, {str(tagline_match.id)})

        total_agents, agent_ids = fetch_ids("competitor")
        self.assertEqual(total_agents, 1)
        self.assertEqual(agent_ids, {str(description_match.id)})

        total_agents, agent_ids = fetch_ids("finance")
        self.assertEqual(total_agents, 1)
        self.assertEqual(agent_ids, {str(category_match.id)})

        response = self.client.get(reverse("pages:library_agents_api"), data={"q": "search-owner"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["totalAgents"], 5)

        total_agents, agent_ids = fetch_ids("BuDgEt")
        self.assertEqual(total_agents, 1)
        self.assertEqual(agent_ids, {str(name_match.id)})

    @tag("batch_public_templates")
    def test_library_api_combines_search_with_category_and_pagination(self):
        user = get_user_model().objects.create_user(
            username="library-search-filter-owner",
            email="library-search-filter-owner@example.com",
            password="pw",
        )
        profile = PublicProfile.objects.create(user=user, handle="search-filter-owner")

        alpha_ops_a = PersistentAgentTemplate.objects.create(
            code="lib-search-alpha-ops-a",
            public_profile=profile,
            slug="alpha-ops-a",
            display_name="Alpha Ops A",
            tagline="Operations alpha",
            description="Operations alpha coverage.",
            charter="Operations alpha coverage.",
            category="Operations",
            is_active=True,
        )
        alpha_ops_b = PersistentAgentTemplate.objects.create(
            code="lib-search-alpha-ops-b",
            public_profile=profile,
            slug="alpha-ops-b",
            display_name="Alpha Ops B",
            tagline="Operations alpha detail",
            description="Operations alpha detail coverage.",
            charter="Operations alpha detail coverage.",
            category="Operations",
            is_active=True,
        )
        alpha_research = PersistentAgentTemplate.objects.create(
            code="lib-search-alpha-research",
            public_profile=profile,
            slug="alpha-research",
            display_name="Alpha Research",
            tagline="Research alpha",
            description="Research alpha coverage.",
            charter="Research alpha coverage.",
            category="Research",
            is_active=True,
        )
        PersistentAgentTemplate.objects.create(
            code="lib-search-beta-ops",
            public_profile=profile,
            slug="beta-ops",
            display_name="Beta Ops",
            tagline="Operations beta",
            description="Operations beta coverage.",
            charter="Operations beta coverage.",
            category="Operations",
            is_active=True,
        )

        query_response = self.client.get(reverse("pages:library_agents_api"), data={"q": "alpha"})
        self.assertEqual(query_response.status_code, 200)
        query_payload = query_response.json()
        self.assertEqual(query_payload["totalAgents"], 3)
        self.assertEqual(query_payload["libraryTotalAgents"], 4)

        category_response = self.client.get(
            reverse("pages:library_agents_api"),
            data={"q": "alpha", "category": "operations"},
        )
        self.assertEqual(category_response.status_code, 200)
        category_payload = category_response.json()
        self.assertEqual(category_payload["totalAgents"], 2)
        self.assertEqual({agent["id"] for agent in category_payload["agents"]}, {str(alpha_ops_a.id), str(alpha_ops_b.id)})

        paged_response = self.client.get(
            reverse("pages:library_agents_api"),
            data={"q": "alpha", "category": "operations", "limit": 1, "offset": 1},
        )
        self.assertEqual(paged_response.status_code, 200)
        paged_payload = paged_response.json()
        self.assertEqual(paged_payload["totalAgents"], 2)
        self.assertEqual(len(paged_payload["agents"]), 1)
        self.assertFalse(paged_payload["hasMore"])
        self.assertEqual(paged_payload["agents"][0]["id"], str(alpha_ops_b.id))

        casefold_response = self.client.get(reverse("pages:library_agents_api"), data={"q": "ALPHA"})
        self.assertEqual(casefold_response.status_code, 200)
        casefold_payload = casefold_response.json()
        self.assertEqual(casefold_payload["totalAgents"], 3)
        self.assertIn(str(alpha_research.id), {agent["id"] for agent in casefold_payload["agents"]})

    @tag("batch_public_templates")
    def test_library_api_orders_by_like_count_for_most_popular_default(self):
        owner = get_user_model().objects.create_user(username="library-like-owner", email="library-like-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=owner, handle="library-like-owner")

        top = PersistentAgentTemplate.objects.create(
            code="lib-like-top",
            public_profile=profile,
            slug="like-top",
            display_name="Like Top",
            tagline="Top liked",
            description="Most liked template.",
            charter="Top liked template.",
            category="Operations",
            is_active=True,
        )
        middle = PersistentAgentTemplate.objects.create(
            code="lib-like-mid",
            public_profile=profile,
            slug="like-mid",
            display_name="Like Middle",
            tagline="Middle liked",
            description="Middle liked template.",
            charter="Middle liked template.",
            category="Operations",
            is_active=True,
        )
        low = PersistentAgentTemplate.objects.create(
            code="lib-like-low",
            public_profile=profile,
            slug="like-low",
            display_name="Like Low",
            tagline="Low liked",
            description="Low liked template.",
            charter="Low liked template.",
            category="Operations",
            is_active=True,
        )

        liker_1 = get_user_model().objects.create_user(username="liker-1", email="liker-1@example.com", password="pw")
        liker_2 = get_user_model().objects.create_user(username="liker-2", email="liker-2@example.com", password="pw")
        liker_3 = get_user_model().objects.create_user(username="liker-3", email="liker-3@example.com", password="pw")

        PersistentAgentTemplateLike.objects.create(template=top, user=liker_1)
        PersistentAgentTemplateLike.objects.create(template=top, user=liker_2)
        PersistentAgentTemplateLike.objects.create(template=top, user=liker_3)
        PersistentAgentTemplateLike.objects.create(template=middle, user=liker_1)

        response = self.client.get(reverse("pages:library_agents_api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        ordered_ids = [agent["id"] for agent in payload["agents"]]
        self.assertEqual(ordered_ids[:3], [str(top.id), str(middle.id), str(low.id)])
        self.assertEqual(payload["libraryTotalLikes"], 4)

    @tag("batch_public_templates")
    def test_library_like_api_requires_authentication(self):
        owner = get_user_model().objects.create_user(username="library-auth-owner", email="library-auth-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=owner, handle="library-auth-owner")
        template = PersistentAgentTemplate.objects.create(
            code="lib-auth-like",
            public_profile=profile,
            slug="auth-like",
            display_name="Auth Like",
            tagline="Auth only",
            description="Auth only like endpoint.",
            charter="Auth only like endpoint.",
            category="Operations",
            is_active=True,
        )

        response = self.client.post(
            reverse("pages:library_agent_like_api"),
            data='{"agentId": "%s"}' % template.id,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(PersistentAgentTemplateLike.objects.filter(template=template).exists())

    @tag("batch_public_templates")
    def test_library_like_api_toggles_and_sets_is_liked_for_authenticated_user(self):
        owner = get_user_model().objects.create_user(username="library-toggle-owner", email="library-toggle-owner@example.com", password="pw")
        profile = PublicProfile.objects.create(user=owner, handle="library-toggle-owner")
        template = PersistentAgentTemplate.objects.create(
            code="lib-toggle-like",
            public_profile=profile,
            slug="toggle-like",
            display_name="Toggle Like",
            tagline="Toggle likes",
            description="Like toggle behavior.",
            charter="Like toggle behavior.",
            category="Operations",
            is_active=True,
        )

        liker = get_user_model().objects.create_user(username="library-liker", email="library-liker@example.com", password="pw")
        self.client.force_login(liker)

        first_toggle = self.client.post(
            reverse("pages:library_agent_like_api"),
            data='{"agentId": "%s"}' % template.id,
            content_type="application/json",
        )
        self.assertEqual(first_toggle.status_code, 200)
        first_payload = first_toggle.json()
        self.assertTrue(first_payload["isLiked"])
        self.assertEqual(first_payload["likeCount"], 1)

        listing_after_like = self.client.get(reverse("pages:library_agents_api")).json()
        first_agent = next(agent for agent in listing_after_like["agents"] if agent["id"] == str(template.id))
        self.assertTrue(first_agent["isLiked"])
        self.assertEqual(first_agent["likeCount"], 1)

        second_toggle = self.client.post(
            reverse("pages:library_agent_like_api"),
            data='{"agentId": "%s"}' % template.id,
            content_type="application/json",
        )
        self.assertEqual(second_toggle.status_code, 200)
        second_payload = second_toggle.json()
        self.assertFalse(second_payload["isLiked"])
        self.assertEqual(second_payload["likeCount"], 0)
