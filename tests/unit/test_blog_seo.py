import json

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings, tag

from proprietary.utils_blog import load_blog_post


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
