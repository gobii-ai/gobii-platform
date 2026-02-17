from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, tag
from django.urls import reverse

from agents.services import PretrainedWorkerTemplateService
from api.models import PersistentAgentTemplate, PersistentAgentTemplateLike, PublicProfile
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
        self.assertContains(response, f'href="{reverse("pages:library")}"')

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
    def test_libary_path_redirects_to_library(self):
        response = self.client.get("/libary/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("pages:library"))

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
