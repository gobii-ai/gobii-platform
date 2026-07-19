import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings, tag
from django.urls import reverse

from pages.agent_api import (
    AGENT_API_CLUSTER_GROUPS,
    AGENT_API_DEVELOPER_DOCS_URL,
    AGENT_API_DOCS_URL,
    AGENT_API_FAQ_ITEMS,
)


@tag("batch_pages")
class AgentAPIPageTests(TestCase):
    def _get_proprietary_page(self):
        response = self.client.get(reverse("pages:agent_api"))
        self.assertEqual(response.status_code, 200)
        return response, BeautifulSoup(response.content, "html.parser")

    def _schema_nodes(self, soup):
        scripts = soup.find_all("script", {"type": "application/ld+json"})
        self.assertEqual(len(scripts), 1)
        schema = json.loads(scripts[0].string)
        self.assertEqual(schema["@context"], "https://schema.org")
        return schema, {node["@type"]: node for node in schema["@graph"]}

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        GOBII_RELEASE_ENV="prod",
        PUBLIC_SITE_URL="https://gobii.ai",
    )
    def test_page_renders_metadata_search_terms_and_product_copy(self):
        response, soup = self._get_proprietary_page()

        expected_url = "https://gobii.ai/agent-api/"
        expected_title = "Agent API for Delegating Real Work | Gobii"
        expected_description = (
            "Use Gobii's Agent API to delegate browser work, research, data entry, and multi-step "
            "workflows to supervised AI agents with visible progress."
        )
        self.assertEqual(soup.title.string, expected_title)
        self.assertGreaterEqual(len(expected_title), 30)
        self.assertLessEqual(len(expected_title), 60)
        self.assertEqual(
            soup.find("meta", attrs={"name": "description"})["content"],
            expected_description,
        )
        self.assertGreaterEqual(len(expected_description), 120)
        self.assertLessEqual(len(expected_description), 160)
        self.assertEqual(soup.find("link", rel="canonical")["href"], expected_url)
        self.assertEqual(soup.find("meta", property="og:url")["content"], expected_url)
        self.assertEqual(soup.find("meta", property="og:title")["content"], expected_title)
        self.assertEqual(
            soup.find("meta", property="og:image")["content"],
            "https://gobii.ai/static/images/solutions/engineering-hero-1280.jpg",
        )
        self.assertNotIn("localhost", response.content.decode("utf-8"))
        self.assertNotIn("http://testserver", response.content.decode("utf-8"))

        self.assertEqual(
            soup.find("h1").get_text(" ", strip=True),
            "Agent API: Outsource Multi-Step Work to AI Agents",
        )
        page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).lower()
        for term in (
            "agentic api",
            "agentic ai api",
            "ai agent api",
            "autonomous agent api",
            "api for ai agents",
            "browser research",
            "data collection and enrichment",
            "recurring monitoring",
            "first-pass qa",
        ):
            with self.subTest(term=term):
                self.assertIn(term, page_text)

        self.assertIn("agent api vs a normal ai api", page_text)
        self.assertIn("autonomous work with explicit boundaries", page_text)
        self.assertIn("create a persistent agent with one request", page_text)
        self.assertGreaterEqual(len(page_text.split()), 1_800)

        cta_forms = soup.find_all(
            "form",
            attrs={"action": reverse("pages:engineering_pro_signup")},
        )
        self.assertEqual(len(cta_forms), 2)
        self.assertTrue(
            all("Start free trial" in form.get_text(" ", strip=True) for form in cta_forms)
        )

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        GOBII_RELEASE_ENV="prod",
        PUBLIC_SITE_URL="https://gobii.ai",
    )
    def test_page_includes_required_structured_data(self):
        _response, soup = self._get_proprietary_page()
        _schema, nodes = self._schema_nodes(soup)

        self.assertEqual(
            set(nodes),
            {
                "Organization",
                "WebSite",
                "WebPage",
                "WebAPI",
                "FAQPage",
                "BreadcrumbList",
                "ItemList",
            },
        )
        self.assertEqual(nodes["WebPage"]["url"], "https://gobii.ai/agent-api/")
        self.assertEqual(nodes["WebPage"]["dateModified"], "2026-07-18")
        self.assertEqual(
            nodes["WebPage"]["mainEntity"],
            {"@id": "https://gobii.ai/agent-api#api"},
        )
        self.assertEqual(nodes["WebAPI"]["name"], "Gobii Agent API")
        self.assertEqual(nodes["WebAPI"]["serviceType"], "Agentic AI API")
        self.assertEqual(
            nodes["WebAPI"]["documentation"],
            "https://docs.gobii.ai/developers/developer-agents",
        )
        self.assertEqual(
            nodes["WebAPI"]["provider"],
            {"@id": "https://gobii.ai#organization"},
        )
        self.assertEqual(
            nodes["WebAPI"]["termsOfService"],
            "https://gobii.ai/tos/",
        )
        self.assertEqual(
            [item["name"] for item in nodes["BreadcrumbList"]["itemListElement"]],
            ["Home", "Agent API: Outsource Multi-Step Work to AI Agents"],
        )
        self.assertEqual(len(nodes["ItemList"]["itemListElement"]), 6)
        self.assertEqual(
            [item["name"] for item in nodes["FAQPage"]["mainEntity"]],
            [item["question"] for item in AGENT_API_FAQ_ITEMS],
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_page_links_live_resources_without_linking_planned_spokes(self):
        _response, soup = self._get_proprietary_page()
        rendered_hrefs = {
            anchor["href"]
            for anchor in soup.find_all("a", href=True)
        }
        rendered_paths = {
            urlparse(anchor["href"]).path
            for anchor in soup.find_all("a", href=True)
        }
        planned_paths = {
            link["url"]
            for group in AGENT_API_CLUSTER_GROUPS
            for link in group["links"]
            if link["status"] == "planned"
        }
        live_urls = {
            link["url"]
            for group in AGENT_API_CLUSTER_GROUPS
            for link in group["links"]
            if link["status"] == "live"
        }

        self.assertTrue(planned_paths.isdisjoint(rendered_paths))
        self.assertFalse(any(path.startswith("/compare/") for path in planned_paths))
        self.assertEqual(
            live_urls,
            {
                "/blog/what-is-an-agentic-api/",
                AGENT_API_DEVELOPER_DOCS_URL,
                AGENT_API_DOCS_URL,
            },
        )
        self.assertTrue(live_urls.issubset(rendered_hrefs))
        self.assertIn(
            reverse("pages:solution", kwargs={"slug": "engineering"}),
            rendered_paths,
        )
        self.assertTrue(
            all(
                link["status"] in {"live", "planned"}
                for group in AGENT_API_CLUSTER_GROUPS
                for link in group["links"]
            )
        )
        self.assertNotIn("Live docs", soup.get_text(" ", strip=True))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_existing_api_entry_points_link_to_agent_api_page(self):
        agent_api_path = reverse("pages:agent_api")

        home_response = self.client.get(reverse("pages:home"))
        self.assertEqual(home_response.status_code, 200)
        home_soup = BeautifulSoup(home_response.content, "html.parser")
        self.assertGreaterEqual(
            len(home_soup.find_all("a", href=agent_api_path)),
            2,
        )

        engineering_response = self.client.get(
            reverse("pages:solution", kwargs={"slug": "engineering"})
        )
        self.assertEqual(engineering_response.status_code, 200)
        engineering_soup = BeautifulSoup(engineering_response.content, "html.parser")
        self.assertIsNotNone(engineering_soup.find("a", href=agent_api_path))

        llms_response = self.client.get("/llms.txt")
        self.assertContains(llms_response, "http://testserver/agent-api/")

    @override_settings(GOBII_PROPRIETARY_MODE=False)
    def test_page_redirects_in_community_mode(self):
        response = self.client.get(reverse("pages:agent_api"))

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/")

    def test_sitemap_visibility_matches_proprietary_mode(self):
        with override_settings(GOBII_PROPRIETARY_MODE=True):
            proprietary_response = self.client.get("/sitemap.xml")
        self.assertEqual(proprietary_response.status_code, 200)
        self.assertIn(
            "<loc>http://example.com/agent-api/</loc>",
            proprietary_response.content.decode(),
        )

        with override_settings(GOBII_PROPRIETARY_MODE=False):
            community_response = self.client.get("/sitemap.xml")
        self.assertEqual(community_response.status_code, 200)
        self.assertNotIn(
            "<loc>http://example.com/agent-api/</loc>",
            community_response.content.decode(),
        )
