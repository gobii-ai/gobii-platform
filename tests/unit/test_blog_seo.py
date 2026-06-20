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
        self.assertEqual(
            soup.find("meta", property="og:image:alt")["content"],
            "Gobii Discord integration",
        )
        self.assertEqual(
            soup.find("meta", attrs={"name": "twitter:image:alt"})["content"],
            "Gobii Discord integration",
        )

        structured_data = json.loads(soup.find("script", type="application/ld+json").string)
        self.assertEqual(structured_data["@type"], "BlogPosting")
        self.assertEqual(structured_data["inLanguage"], "en-US")
        self.assertEqual(structured_data["isPartOf"]["name"], "Gobii Blog")
        self.assertEqual(structured_data["keywords"], ["newsletter", "weekly", "product-updates"])
        self.assertGreater(structured_data["wordCount"], 0)
        self.assertEqual(structured_data["image"], structured_data["thumbnailUrl"])

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

        structured_data = json.loads(soup.find("script", type="application/ld+json").string)
        self.assertEqual(structured_data["@type"], "Blog")
        self.assertEqual(structured_data["name"], "Gobii AI Agent Automation Blog")
        self.assertEqual(structured_data["inLanguage"], "en-US")
        self.assertIn("AI agent automation", structured_data["keywords"])
        self.assertGreaterEqual(len(structured_data["about"]), 5)
        self.assertGreaterEqual(len(structured_data["blogPost"]), 40)
        self.assertIn("description", structured_data["blogPost"][0])
        self.assertIn("author", structured_data["blogPost"][0])
