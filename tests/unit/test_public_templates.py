from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, tag
from django.urls import reverse

from agents.services import PretrainedWorkerTemplateService
from api.models import PersistentAgentTemplate, PublicProfile
from api.public_profiles import validate_public_handle
from pages.library_views import LIBRARY_CACHE_KEY


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


class LibraryViewsTests(TestCase):
    def setUp(self):
        cache.delete(LIBRARY_CACHE_KEY)

    @tag("batch_public_templates")
    def test_library_page_renders_react_mount(self):
        response = self.client.get(reverse("pages:library"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="gobii-frontend-root"')
        self.assertContains(response, 'data-app="library"')

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
            reverse("pages:public_template_detail", kwargs={"handle": "library-owner", "template_slug": "ops-automator"}),
        )

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
