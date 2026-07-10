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

    def test_blog_post_wraps_markdown_tables_for_responsive_layout(self):
        post = load_blog_post("hire-ai-employees")
        soup = BeautifulSoup(post["html"], "html.parser")
        wrappers = soup.select(".blog-table-scroll")

        self.assertEqual(len(wrappers), 2)
        self.assertIn("blog-table-scroll--wide", wrappers[0].get("class", []))
        self.assertIn("blog-table-scroll--compact", wrappers[1].get("class", []))

        for wrapper in wrappers:
            self.assertEqual(wrapper.get("role"), "region")
            self.assertEqual(wrapper.get("aria-label"), "Scrollable data table")
            self.assertEqual(wrapper.get("tabindex"), "0")
            self.assertIsNotNone(wrapper.find("table", recursive=False))

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
    def test_best_ai_employees_blog_post_renders_seo_and_required_links(self):
        response = self.client.get("/blog/best-ai-employees/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")

        expected_title = "Best AI Employees: 2026 Platform Guide | Gobii"
        expected_description = (
            "Compare 9 leading AI employee platforms for workflow ownership, integrations, "
            "human oversight, governance, safe business deployment, and buyer testing in 2026."
        )
        self.assertLessEqual(len(expected_title), 60)
        self.assertGreaterEqual(len(expected_description), 150)
        self.assertLessEqual(len(expected_description), 160)
        self.assertEqual(
            soup.find("title").get_text(strip=True),
            expected_title,
        )
        self.assertEqual(
            soup.find("meta", attrs={"name": "description"})["content"],
            expected_description,
        )
        self.assertEqual(
            soup.find("meta", property="og:title")["content"],
            expected_title,
        )
        self.assertEqual(
            soup.find("meta", property="og:description")["content"],
            expected_description,
        )
        og_image = soup.find("meta", property="og:image")["content"]
        twitter_image = soup.find("meta", attrs={"name": "twitter:image"})["content"]
        self.assertTrue(og_image.endswith("/static/images/blog/best-ai-employees-social.png"))
        self.assertEqual(twitter_image, og_image)
        self.assertEqual(soup.find("meta", property="og:image:type")["content"], "image/png")
        self.assertEqual(soup.find("meta", property="og:image:width")["content"], "1200")
        self.assertEqual(soup.find("meta", property="og:image:height")["content"], "630")
        self.assertEqual(
            soup.find("meta", property="og:image:alt")["content"],
            "Comparison graphic showing nine AI employee platforms evaluated by workflow ownership, oversight, governance, integrations, and handoff quality.",
        )
        self.assertEqual(
            soup.find("link", rel="canonical")["href"],
            "http://testserver/blog/best-ai-employees/",
        )

        rendered_hrefs = {
            link.get("href")
            for link in soup.find_all("a")
            if link.get("href")
        }
        self.assertIn("/ai-employees/", rendered_hrefs)
        self.assertIn("/solutions/sales/ai-sales-agent/", rendered_hrefs)
        self.assertIn("/blog/hire-ai-employees/", rendered_hrefs)
        missing_cluster_paths = {
            "/blog/ai-employee-app/",
            "/blog/ai-employee-company/",
            "/blog/what-is-an-ai-employee/",
            "/blog/ai-workers/",
            "/blog/ai-teammates/",
            "/blog/ai-employees-vs-ai-agents/",
            "/blog/ai-agent-examples/",
            "/blog/ai-agents-for-business/",
            "/blog/custom-ai-agents-for-business/",
            "/blog/ai-employees-for-business/",
        }
        self.assertFalse(missing_cluster_paths & rendered_hrefs)
        self.assertIn("https://www.shrm.org/media-only/navigating-ai-in-the-workplace", rendered_hrefs)
        self.assertIn(
            "https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027",
            rendered_hrefs,
        )
        self.assertContains(response, "Gayle Oeschger")
        self.assertContains(response, "Last reviewed July 9, 2026")
        self.assertContains(response, "How we weighted the comparison")
        self.assertContains(response, "official provider pages and public positioning")
        self.assertContains(response, "Competitor feature descriptions are sourced from public provider pages")
        self.assertContains(response, "In its 2025 forecast")
        self.assertNotContains(response, "Microsoft, 2026 Work Trend Index")

        structured_data = json.loads(soup.find("script", type="application/ld+json").string)
        nodes = {node["@type"]: node for node in structured_data["@graph"]}
        self.assertEqual(
            set(nodes),
            {
                "BlogPosting",
                "Person",
                "Organization",
                "ImageObject",
                "BreadcrumbList",
                "FAQPage",
            },
        )

        article_schema = nodes["BlogPosting"]
        self.assertEqual(article_schema["headline"], expected_title)
        self.assertEqual(article_schema["description"], expected_description)
        self.assertEqual(article_schema["url"], "http://testserver/blog/best-ai-employees/")
        self.assertIn("best ai employees", article_schema["keywords"])
        self.assertEqual(article_schema["author"], {"@id": nodes["Person"]["@id"]})

        image_schema = nodes["ImageObject"]
        self.assertEqual(image_schema["width"], 1200)
        self.assertEqual(image_schema["height"], 630)
        self.assertTrue(
            image_schema["url"].endswith("/static/images/blog/best-ai-employees-social.png")
        )

        breadcrumb_schema = nodes["BreadcrumbList"]
        self.assertEqual(
            breadcrumb_schema["itemListElement"][-1]["name"],
            "Best AI Employees: 2026 Platform Guide",
        )

        person_schema = nodes["Person"]
        self.assertEqual(person_schema["name"], "Gayle Oeschger")
        self.assertEqual(
            person_schema["worksFor"],
            {"@id": nodes["Organization"]["@id"]},
        )

        faq_schema = nodes["FAQPage"]
        self.assertEqual(
            [item["name"] for item in faq_schema["mainEntity"]],
            [
                "Which AI employee platform is best?",
                "What should you look for in an AI employee?",
                "Are AI employees the same as AI agents?",
                "Can AI employees handle full business functions?",
            ],
        )
        self.assertTrue(
            all(
                item["acceptedAnswer"]["@type"] == "Answer"
                and item["acceptedAnswer"]["text"]
                for item in faq_schema["mainEntity"]
            )
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_hire_ai_employees_blog_post_renders_seo_and_only_live_cluster_links(self):
        response = self.client.get("/blog/hire-ai-employees/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")

        expected_title = "How to Hire AI Employees for Business Workflows | Gobii"
        expected_description = (
            "Learn how to hire AI employees by choosing workflows, setting permissions, "
            "creating human review loops, and deploying supervised AI teammates safely at work."
        )
        self.assertEqual(soup.find("title").get_text(strip=True), expected_title)
        self.assertEqual(
            soup.find("meta", attrs={"name": "description"})["content"],
            expected_description,
        )
        self.assertEqual(soup.find("meta", property="og:title")["content"], expected_title)
        og_image = soup.find("meta", property="og:image")["content"]
        twitter_image = soup.find("meta", attrs={"name": "twitter:image"})["content"]
        self.assertTrue(
            og_image.endswith("/static/images/blog/ai-employee-workflow-review-loop.png")
        )
        self.assertEqual(twitter_image, og_image)
        self.assertEqual(soup.find("meta", property="og:image:type")["content"], "image/png")
        self.assertEqual(soup.find("meta", property="og:image:width")["content"], "1200")
        self.assertEqual(soup.find("meta", property="og:image:height")["content"], "630")
        self.assertEqual(
            soup.find("link", rel="canonical")["href"],
            "http://testserver/blog/hire-ai-employees/",
        )
        self.assertContains(response, "Gayle Oeschger")

        article_hrefs = {
            link.get("href")
            for link in soup.select_one(".prose").find_all("a")
            if link.get("href")
        }
        self.assertIn("/ai-employees/", article_hrefs)
        self.assertIn("/blog/best-ai-employees/", article_hrefs)
        self.assertIn("/blog/newsletter-2026-06-09-browser-intelligence/", article_hrefs)

        article_image = soup.select_one(
            '.prose img[src="/static/images/blog/ai-employee-workflow-review-loop.svg"]'
        )
        self.assertIsNotNone(article_image)
        self.assertContains(response, "During Gobii's implementation of browser workflows")
        self.assertContains(response, "In its 2023")
        self.assertContains(response, "In its 2024")

        rendered_hrefs = {
            link.get("href")
            for link in soup.find_all("a")
            if link.get("href")
        }
        missing_cluster_paths = {
            "/blog/ai-employee-app/",
            "/blog/ai-employee-company/",
            "/blog/what-is-an-ai-employee/",
            "/blog/ai-workers/",
            "/blog/ai-teammates/",
            "/blog/ai-employees-vs-ai-agents/",
            "/blog/ai-agent-examples/",
            "/blog/ai-agents-for-business/",
            "/blog/custom-ai-agents-for-business/",
            "/blog/ai-employees-for-business/",
        }
        self.assertFalse(missing_cluster_paths & rendered_hrefs)

        structured_data = json.loads(soup.find("script", type="application/ld+json").string)
        nodes = {node["@type"]: node for node in structured_data["@graph"]}
        self.assertEqual(
            set(nodes),
            {
                "BlogPosting",
                "Person",
                "Organization",
                "ImageObject",
                "BreadcrumbList",
                "FAQPage",
            },
        )

        article_schema = nodes["BlogPosting"]
        self.assertEqual(article_schema["headline"], expected_title)
        self.assertEqual(article_schema["description"], expected_description)
        self.assertIn("hire ai employees", article_schema["keywords"])
        self.assertEqual(article_schema["author"], {"@id": nodes["Person"]["@id"]})

        person_schema = nodes["Person"]
        self.assertEqual(person_schema["name"], "Gayle Oeschger")
        self.assertEqual(
            person_schema["worksFor"],
            {"@id": nodes["Organization"]["@id"]},
        )

        faq_schema = nodes["FAQPage"]
        self.assertEqual(
            [item["name"] for item in faq_schema["mainEntity"]],
            [
                "How do you hire AI employees?",
                "What should an AI employee do first?",
                "How do you supervise AI employees?",
                "How long does deployment take?",
            ],
        )
        self.assertTrue(
            all(
                item["acceptedAnswer"]["@type"] == "Answer"
                and item["acceptedAnswer"]["text"]
                for item in faq_schema["mainEntity"]
            )
        )

        image_schema = nodes["ImageObject"]
        self.assertEqual(image_schema["width"], 1200)
        self.assertEqual(image_schema["height"], 630)
        self.assertTrue(
            image_schema["url"].endswith(
                "/static/images/blog/ai-employee-workflow-review-loop.png"
            )
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_editorial_policy_is_published_and_linked_from_footer(self):
        response = self.client.get("/editorial-policy/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        self.assertEqual(soup.find("title").get_text(strip=True), "Editorial Policy | Gobii")
        self.assertEqual(soup.find("h1").get_text(strip=True), "Editorial Policy")
        self.assertContains(response, "Sources and evidence")
        self.assertContains(response, "Updates and corrections")

        footer_link = soup.select_one('footer a[href="/editorial-policy/"]')
        self.assertIsNotNone(footer_link)
        self.assertEqual(footer_link.get_text(" ", strip=True), "Editorial Policy")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_hire_ai_employees_blog_post_omits_unneeded_static_page_scripts(self):
        response = self.client.get("/blog/hire-ai-employees/")

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        asset_urls = [
            element.get(attribute)
            for element, attribute in (
                *((script, "src") for script in soup.find_all("script", src=True)),
                *((link, "href") for link in soup.find_all("link", href=True)),
            )
        ]

        for fragment in (
            "preline",
            "htmx",
            "lightbox",
            "stripe",
            "phone_format",
            "account_identity_signals",
            "account_auth_forms",
            "cta_signup_modal",
            "cta_tracking",
            "signup_tracking",
        ):
            with self.subTest(fragment=fragment):
                self.assertFalse(any(fragment in url for url in asset_urls))

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
        self.assertContains(response, "/blog/best-ai-employees/")
        self.assertContains(response, "/blog/hire-ai-employees/")
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
        self.assertIn(
            "http://testserver/blog/best-ai-employees/",
            {post["url"] for post in structured_data["blogPost"]},
        )
        self.assertIn(
            "http://testserver/blog/hire-ai-employees/",
            {post["url"] for post in structured_data["blogPost"]},
        )
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
