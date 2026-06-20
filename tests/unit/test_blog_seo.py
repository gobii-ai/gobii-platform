import json
from datetime import timedelta

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from proprietary.utils_blog import BLOGS_ROOT, _parse_svg_length, get_all_blog_posts, load_blog_post


@tag("batch_pages")
class BlogSeoTests(TestCase):
    def tearDown(self):
        get_all_blog_posts.cache_clear()
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
        self.assertEqual(structured_data["author"]["@type"], "Organization")
        self.assertEqual(structured_data["author"]["name"], "The Gobii Team")
        self.assertEqual(structured_data["inLanguage"], "en-US")
        self.assertEqual(structured_data["isPartOf"]["name"], "Gobii Blog")
        self.assertEqual(structured_data["keywords"], ["newsletter", "weekly", "product-updates"])
        self.assertGreater(structured_data["wordCount"], 0)
        self.assertEqual(structured_data["image"], structured_data["thumbnailUrl"])

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_blog_post_uses_default_social_alt_for_default_social_image(self):
        response = self.client.get("/blog/how-we-sandbox-ai-agents-in-production/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        og_image = soup.find("meta", property="og:image")["content"]
        og_image_alt = soup.find("meta", property="og:image:alt")["content"]

        self.assertTrue(og_image.endswith("/static/images/noBgBlue.png"))
        self.assertEqual(og_image_alt, "Gobii logo")
        self.assertEqual(
            soup.find("meta", attrs={"name": "twitter:image:alt"})["content"],
            "Gobii logo",
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_always_on_ai_agents_article_renders_with_local_svg(self):
        response = self.client.get("/blog/what-are-always-on-ai-agents/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        title = soup.find("title").get_text(strip=True)
        meta_description = soup.find("meta", attrs={"name": "description"})["content"]
        first_image = soup.find("img", src="/static/images/blog/always-on-ai-agents-workflow.svg")

        self.assertEqual(
            title,
            "Always-On AI Agents: Persistent AI Explained - Gobii",
        )
        self.assertLessEqual(len(title), 60)
        self.assertEqual(len(meta_description), 155)
        self.assertEqual(
            [heading.get_text(" ", strip=True) for heading in soup.find_all("h1")],
            ["What Are Always-On AI Agents?"],
        )
        self.assertIsNotNone(first_image)
        self.assertEqual(first_image["width"], "1200")
        self.assertEqual(first_image["height"], "630")
        self.assertEqual(first_image["loading"], "eager")
        self.assertEqual(first_image["fetchpriority"], "high")
        self.assertContains(response, "Key Takeaways")
        self.assertContains(response, "/blog/how-we-sandbox-ai-agents-in-production/")
        self.assertContains(response, "About the author")
        self.assertContains(response, "Founder of Gobii")
        self.assertContains(response, "Author profile")
        self.assertNotContains(response, "lightbox2/2.11.5")
        self.assertNotContains(response, "citation capsule")
        self.assertIsNotNone(
            soup.find("a", href="https://hai.stanford.edu/ai-index/2025-ai-index-report")
        )

        structured_data_scripts = [
            json.loads(script.string)
            for script in soup.find_all("script", type="application/ld+json")
        ]
        structured_data = structured_data_scripts[0]
        self.assertEqual(structured_data["@type"], "BlogPosting")
        self.assertEqual(structured_data["author"]["@type"], "Person")
        self.assertEqual(structured_data["author"]["name"], "Andrew I. Christianson")
        self.assertEqual(structured_data["author"]["jobTitle"], "Founder of Gobii")
        self.assertEqual(structured_data["author"]["url"], "http://testserver/about/")
        self.assertEqual(structured_data["author"]["@id"], "http://testserver/about#person")
        self.assertEqual(structured_data["author"]["worksFor"]["name"], "Gobii")
        self.assertIn("browser automation", structured_data["author"]["knowsAbout"])
        self.assertIn("runtime patterns", structured_data["author"]["description"])
        self.assertEqual(
            structured_data["keywords"],
            ["ai agents", "persistent agents", "automation", "memory", "webhooks"],
        )
        self.assertGreater(structured_data["wordCount"], 1800)

        faq_schema = next(
            script for script in structured_data_scripts if script.get("@type") == "FAQPage"
        )
        self.assertEqual(len(faq_schema["mainEntity"]), 5)
        self.assertEqual(
            faq_schema["mainEntity"][0]["name"],
            "Are always-on AI agents autonomous?",
        )
        self.assertIn(
            "scoped tools",
            faq_schema["mainEntity"][0]["acceptedAnswer"]["text"],
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_blog_detail_only_loads_lightbox_when_content_uses_it(self):
        response = self.client.get("/blog/project-management-test-case/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-lightbox="gobii-case"')
        self.assertContains(response, "lightbox2/2.11.5")

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

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_future_dated_blog_posts_are_hidden_until_publish_date(self):
        slug = "_scheduled-test-post"
        path = BLOGS_ROOT / f"{slug}.md"
        future_date = timezone.localdate() + timedelta(days=7)
        path.write_text(
            "\n".join(
                [
                    "---",
                    'title: "Scheduled Test Post"',
                    f'date: "{future_date.isoformat()}"',
                    'description: "A post that should stay hidden until its date arrives."',
                    'author: "The Gobii Team"',
                    "---",
                    "",
                    "This future post should not be public yet.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.addCleanup(path.unlink, missing_ok=True)
        get_all_blog_posts.cache_clear()
        load_blog_post.cache_clear()

        posts = get_all_blog_posts()
        self.assertNotIn(slug, [post["slug"] for post in posts])

        index_response = self.client.get("/blog/")
        self.assertEqual(index_response.status_code, 200)
        self.assertNotContains(index_response, "Scheduled Test Post")

        detail_response = self.client.get(f"/blog/{slug}/")
        self.assertEqual(detail_response.status_code, 404)

        sitemap_response = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap_response.status_code, 200)
        self.assertNotContains(sitemap_response, f"/blog/{slug}/")
