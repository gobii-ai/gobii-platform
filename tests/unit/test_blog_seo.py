import json

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings, tag

from proprietary.utils_blog import _parse_svg_length, load_blog_post


@tag("batch_pages")
class BlogSeoTests(TestCase):
    def tearDown(self):
        load_blog_post.cache_clear()

    def test_blog_post_enriches_local_images_for_cwv(self):
        post = load_blog_post("gobii-vs-openclaw")
        soup = BeautifulSoup(post["html"], "html.parser")
        images = soup.find_all("img")

        self.assertGreaterEqual(len(images), 2)
        first_image = images[0]
        second_image = images[1]

        self.assertTrue(first_image.get("width", "").isdigit())
        self.assertTrue(first_image.get("height", "").isdigit())
        self.assertEqual(first_image.get("loading"), "eager")
        self.assertEqual(first_image.get("decoding"), "async")
        self.assertEqual(first_image.get("fetchpriority"), "high")

        self.assertTrue(second_image.get("width", "").isdigit())
        self.assertTrue(second_image.get("height", "").isdigit())
        self.assertEqual(second_image.get("loading"), "lazy")
        self.assertEqual(second_image.get("decoding"), "async")

    def test_svg_length_parser_ignores_relative_units(self):
        self.assertEqual(_parse_svg_length("100"), 100)
        self.assertEqual(_parse_svg_length("100px"), 100)
        self.assertIsNone(_parse_svg_length("100%"))
        self.assertIsNone(_parse_svg_length("10em"))
        self.assertIsNone(_parse_svg_length("50vw"))

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_blog_post_renders_social_alt_and_structured_data(self):
        response = self.client.get("/blog/newsletter-2026-06-02-discord-integration/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        image_alt = "A persistent Gobii AI agent posting a daily bug briefing inside a Discord channel"
        self.assertEqual(
            soup.find("meta", property="og:image:alt")["content"],
            image_alt,
        )
        self.assertEqual(
            soup.find("meta", attrs={"name": "twitter:image:alt"})["content"],
            image_alt,
        )

        structured_data = json.loads(soup.find("script", type="application/ld+json").string)
        article = next(item for item in structured_data["@graph"] if item["@type"] == "BlogPosting")
        faq_page = next(item for item in structured_data["@graph"] if item["@type"] == "FAQPage")
        self.assertEqual(article["inLanguage"], "en-US")
        self.assertEqual(article["isPartOf"]["name"], "Gobii Blog")
        self.assertEqual(
            article["keywords"],
            [
                "newsletter",
                "weekly",
                "product-updates",
                "discord-ai-agent",
                "integrations",
                "collaboration",
            ],
        )
        self.assertEqual(article["author"]["name"], "Will Bonde")
        self.assertEqual(len(faq_page["mainEntity"]), 4)
        self.assertGreater(article["wordCount"], 0)
        self.assertEqual(article["image"], article["thumbnailUrl"])
        self.assertTrue(article["image"].endswith("newsletter-2026-06-02-discord-integration-hero.webp"))
        self.assertContains(response, "Updated July 16, 2026")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_blog_post_uses_default_social_alt_for_default_social_image(self):
        response = self.client.get("/blog/how-we-sandbox-ai-agents-in-production/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        og_image = soup.find("meta", property="og:image")["content"]
        og_image_alt = soup.find("meta", property="og:image:alt")["content"]

        self.assertTrue(og_image.endswith("/static/images/gobii_fish_social_1280x640.png"))
        self.assertEqual(og_image_alt, "Gobii logo")
        self.assertEqual(
            soup.find("meta", attrs={"name": "twitter:image:alt"})["content"],
            "Gobii logo",
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_blog_post_renders_faq_graph_and_named_author_metadata(self):
        response = self.client.get("/blog/newsletter-2026-04-08-inbound-webhooks/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        structured_data = json.loads(soup.find("script", type="application/ld+json").string)
        graph = structured_data["@graph"]
        article = next(item for item in graph if item["@type"] == "BlogPosting")
        faq_page = next(item for item in graph if item["@type"] == "FAQPage")

        self.assertEqual(article["author"]["@type"], "Person")
        self.assertEqual(article["author"]["name"], "Will Bonde")
        self.assertEqual(article["author"]["jobTitle"], "Growth & Engineering")
        self.assertTrue(article["author"]["url"].endswith("/team/"))
        self.assertEqual(len(faq_page["mainEntity"]), 4)
        self.assertContains(response, "Updated July 16, 2026")
        self.assertContains(response, faq_page["mainEntity"][0]["acceptedAnswer"]["text"])

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_blog_index_renders_topic_hub_metadata_and_structured_data(self):
        response = self.client.get("/blog/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        title = soup.find("title").get_text(strip=True)
        meta_description = soup.find("meta", attrs={"name": "description"})["content"]

        self.assertEqual(
            title,
            "AI Agent Automation, Browser Agents, and MCP Blog - Gobii",
        )
        self.assertEqual(len(title), 57)
        self.assertEqual(len(meta_description), 149)
        self.assertEqual(
            [heading.get_text(" ", strip=True) for heading in soup.find_all("h1")],
            ["AI Agent Automation Blog"],
        )
        self.assertContains(response, "Explore by topic")
        self.assertContains(response, "Production safety")
        self.assertContains(response, "/blog/how-we-sandbox-ai-agents-in-production/")
        self.assertContains(response, "bg-white py-12")
        self.assertNotContains(response, "bg-sky-950")
        self.assertNotContains(response, "text-cyan-50")
        self.assertNotContains(response, "bg-[#")

        integrations_section = next(
            section
            for section in response.context["topic_sections"]
            if section["name"] == "MCP and integrations"
        )
        self.assertIn(
            "newsletter-2026-04-08-inbound-webhooks",
            [post["slug"] for post in integrations_section["posts"]],
        )
        self.assertIn(
            "newsletter-2026-06-02-discord-integration",
            [post["slug"] for post in integrations_section["posts"]],
        )

        structured_data = json.loads(soup.find("script", type="application/ld+json").string)
        self.assertEqual(structured_data["@type"], "Blog")
        self.assertEqual(structured_data["name"], "Gobii AI Agent Automation Blog")
        self.assertEqual(structured_data["inLanguage"], "en-US")
        self.assertIn("AI agent automation", structured_data["keywords"])
        self.assertGreaterEqual(len(structured_data["about"]), 5)
        self.assertGreaterEqual(len(structured_data["blogPost"]), 40)
        self.assertIn("description", structured_data["blogPost"][0])
        self.assertIn("author", structured_data["blogPost"][0])
        inbound_webhooks = next(
            post
            for post in structured_data["blogPost"]
            if post["url"].endswith("/blog/newsletter-2026-04-08-inbound-webhooks/")
        )
        self.assertEqual(inbound_webhooks["author"]["@type"], "Person")
        self.assertEqual(inbound_webhooks["author"]["name"], "Will Bonde")
        self.assertEqual(inbound_webhooks["author"]["jobTitle"], "Growth & Engineering")
        self.assertTrue(inbound_webhooks["author"]["url"].endswith("/team/"))
