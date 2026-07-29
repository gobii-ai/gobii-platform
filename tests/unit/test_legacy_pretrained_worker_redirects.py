import csv
import importlib
from pathlib import Path

from bs4 import BeautifulSoup
from django.apps import apps as django_apps
from django.test import TestCase, override_settings, tag

from api.models import PersistentAgentTemplate
from pages.legacy_pretrained_worker_redirects import (
    LEGACY_PRETRAINED_WORKER_REDIRECTS,
)


@tag("batch_pages")
class CanonicalTemplateMigrationTests(TestCase):
    def test_migration_creates_distinct_pages_and_merges_legacy_copy(self):
        migration = importlib.import_module(
            "api.migrations.0442_retire_pretrained_worker_pages"
        )
        exact_legacy_template = PersistentAgentTemplate.objects.create(
            code="project-manager",
            display_name="Project Manager",
            tagline="Coordinate projects.",
            description="Keep project work moving.",
            charter="Coordinate the project.",
            category="Team Ops",
            is_official=False,
            is_active=True,
        )
        retired_talent_sourcer = PersistentAgentTemplate.objects.create(
            code="talent-sourcer",
            display_name="Talent Sourcer",
            tagline="Build candidate lists.",
            description="Find candidates and prepare outreach.",
            charter="Source candidates.",
            category="People",
            show_on_homepage=True,
            is_active=True,
        )

        migration.ensure_canonical_templates(django_apps, schema_editor=None)

        exact_legacy_template.refresh_from_db()
        retired_talent_sourcer.refresh_from_db()
        self.assertTrue(exact_legacy_template.is_official)
        self.assertEqual(exact_legacy_template.slug, "project-manager")
        self.assertEqual(exact_legacy_template.category, "Team Ops")
        self.assertFalse(retired_talent_sourcer.is_active)
        self.assertFalse(retired_talent_sourcer.show_on_homepage)
        candidate_researcher = PersistentAgentTemplate.objects.get(
            code="candidate-researcher"
        )
        outreach_agent = PersistentAgentTemplate.objects.get(code="outreach-agent")
        self.assertEqual(candidate_researcher.slug, "candidate-researcher")
        self.assertEqual(candidate_researcher.category, "Recruiting")
        self.assertIn("source-linked profile", candidate_researcher.description)
        self.assertEqual(outreach_agent.slug, "outreach-agent")
        self.assertEqual(outreach_agent.category, "Sales")
        self.assertIn("human approval", outreach_agent.charter)

        for code, merged_copy in migration.MERGED_CUSTOMIZATION_NOTES.items():
            with self.subTest(code=code):
                template = PersistentAgentTemplate.objects.get(code=code)
                self.assertIn(merged_copy, template.customization_notes)


@tag("batch_pages")
class LegacyPretrainedWorkerRedirectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        destination_pairs = {
            (
                redirect_config.destination_category_slug,
                redirect_config.destination_template_slug,
            )
            for redirect_config in LEGACY_PRETRAINED_WORKER_REDIRECTS.values()
        }
        for position, (category_slug, template_slug) in enumerate(sorted(destination_pairs)):
            display_name = template_slug.replace("-", " ").title()
            description = f"Canonical AI employee template for {display_name}."
            charter = f"Run the canonical {display_name} workflow."
            if template_slug == "candidate-researcher":
                display_name = "Candidate Researcher"
                description = (
                    "Build deeper candidate profiles with source-linked work history and public evidence "
                    "for human review."
                )
                charter = "Research candidate profiles and return evidence for human review."
            elif template_slug == "outreach-agent":
                display_name = "Outreach Agent"
                description = (
                    "Draft personalized outreach and organize follow-ups while preserving human approval."
                )
                charter = "Prepare source-backed outreach drafts for human approval."

            PersistentAgentTemplate.objects.create(
                code=f"legacy-redirect-destination-{position}",
                slug=template_slug,
                display_name=display_name,
                tagline=f"Start the {display_name} workflow.",
                description=description,
                charter=charter,
                category=category_slug.replace("-", " ").title(),
                is_official=True,
                is_active=True,
            )

    def test_redirect_manifest_matches_every_known_generated_url(self):
        manifest_path = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "seo"
            / "pretrained-workers-redirect-manifest.csv"
        )
        with manifest_path.open(newline="", encoding="utf-8") as manifest_file:
            rows = list(csv.DictReader(manifest_file))

        expected = {
            "/pretrained-workers/": (
                "/library/",
                "exact_duplicate",
            ),
        }
        for legacy_slug, redirect_config in LEGACY_PRETRAINED_WORKER_REDIRECTS.items():
            legacy_base = f"/pretrained-workers/{legacy_slug}/"
            expected[legacy_base] = (
                redirect_config.detail_path(),
                redirect_config.resolution_type,
            )
            expected[f"{legacy_base}hire/"] = (
                redirect_config.hire_path(),
                redirect_config.resolution_type,
            )
            expected[f"{legacy_base}spawn/"] = (
                redirect_config.launch_path(),
                redirect_config.resolution_type,
            )

        actual = {
            row["legacy_url"]: (
                row["destination_url"],
                row["resolution_type"],
            )
            for row in rows
        }
        self.assertEqual(actual, expected)
        self.assertTrue(all(row["notes"].strip() for row in rows))

    def test_directory_redirects_permanently_to_library(self):
        response = self.client.get("/pretrained-workers/?q=sales")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/library/")
        self.assertEqual(response.content, b"")

    def test_every_known_detail_redirect_is_permanent_and_one_hop(self):
        for legacy_slug, redirect_config in LEGACY_PRETRAINED_WORKER_REDIRECTS.items():
            with self.subTest(legacy_slug=legacy_slug):
                response = self.client.get(
                    f"/pretrained-workers/{legacy_slug}/",
                    {"utm_source": "legacy"},
                )

                self.assertEqual(response.status_code, 301)
                self.assertEqual(
                    response["Location"],
                    f"{redirect_config.detail_path()}?utm_source=legacy",
                )
                self.assertEqual(response.content, b"")

    def test_database_backed_cutover_url_redirects_but_new_unknown_code_does_not(self):
        response = self.client.get("/pretrained-workers/tpl-f2c5bb1cdb34/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/library/sales/lead-hunter/")
        self.assertEqual(
            self.client.get("/pretrained-workers/tpl-created-after-cutover/").status_code,
            404,
        )

    def test_legacy_hire_uses_308_to_preserve_post(self):
        redirect_config = LEGACY_PRETRAINED_WORKER_REDIRECTS["lead-hunter"]

        response = self.client.post(
            "/pretrained-workers/lead-hunter/hire/?utm_source=legacy",
            {"source_page": "old-link"},
        )

        self.assertEqual(response.status_code, 308)
        self.assertEqual(
            response["Location"],
            f"{redirect_config.hire_path()}?utm_source=legacy",
        )
        self.assertEqual(response.content, b"")

    def test_legacy_spawn_redirects_permanently_to_canonical_spawn(self):
        redirect_config = LEGACY_PRETRAINED_WORKER_REDIRECTS["talent-scout"]

        response = self.client.get("/pretrained-workers/talent-scout/spawn/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], redirect_config.launch_path())
        self.assertEqual(response.content, b"")

    def test_retired_talent_sourcer_library_urls_redirect_to_candidate_sourcing(self):
        cases = (
            (
                "get",
                "/library/people/talent-sourcer/?utm_source=legacy",
                301,
                "/library/recruiting/candidate-sourcing-agent/?utm_source=legacy",
            ),
            (
                "post",
                "/library/people/talent-sourcer/hire/?utm_source=legacy",
                308,
                "/library/recruiting/candidate-sourcing-agent/hire/?utm_source=legacy",
            ),
            (
                "get",
                "/library/people/talent-sourcer/spawn/?utm_source=legacy",
                301,
                "/library/recruiting/candidate-sourcing-agent/spawn/?utm_source=legacy",
            ),
        )

        for method_name, path, status_code, destination in cases:
            with self.subTest(path=path):
                response = getattr(self.client, method_name)(path)
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response["Location"], destination)
                self.assertEqual(response.content, b"")

    def test_unknown_legacy_slugs_return_404(self):
        PersistentAgentTemplate.objects.create(
            code="arbitrary-community-code",
            slug="arbitrary-community-template",
            display_name="Arbitrary Community Template",
            tagline="Should not become a known legacy route.",
            description="Unknown legacy codes must not redirect.",
            charter="Do work.",
            category="Operations",
            is_active=True,
        )

        cases = (
            ("get", "/pretrained-workers/arbitrary-community-code/"),
            ("post", "/pretrained-workers/arbitrary-community-code/hire/"),
            ("get", "/pretrained-workers/arbitrary-community-code/spawn/"),
            ("get", "/pretrained-workers/does-not-exist/"),
        )
        for method_name, path in cases:
            with self.subTest(path=path):
                response = getattr(self.client, method_name)(path)
                self.assertEqual(response.status_code, 404)

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        GOBII_RELEASE_ENV="prod",
        PUBLIC_SITE_URL="https://gobii.ai",
    )
    def test_new_distinct_destinations_are_canonical_library_pages(self):
        cases = (
            (
                "/library/recruiting/candidate-researcher/",
                "Candidate Researcher",
                "candidate profiles",
            ),
            (
                "/library/sales/outreach-agent/",
                "Outreach Agent",
                "human approval",
            ),
        )

        for path, heading, differentiated_copy in cases:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, heading)
                self.assertContains(response, differentiated_copy)
                self.assertNotContains(response, "/pretrained-workers/")

                soup = BeautifulSoup(response.content, "html.parser")
                canonical = soup.find("link", rel="canonical")
                self.assertIsNotNone(canonical)
                self.assertEqual(canonical["href"], f"https://gobii.ai{path}")

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    def test_internal_marketing_surfaces_only_link_to_library_templates(self):
        for path in (
            "/",
            "/solutions/",
            "/solutions/recruiting/",
            "/solutions/recruiting/candidate-sourcing/",
            "/solutions/sales/",
            "/solutions/sales/ai-sales-agent/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "/pretrained-workers/")

    @override_settings(
        GOBII_PROPRIETARY_MODE=True,
        PUBLIC_SITE_URL="https://gobii.ai",
    )
    def test_sitemap_contains_canonical_library_urls_only(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn("<loc>http://example.com/pretrained-workers/", content)
        self.assertIn(
            "<loc>http://example.com/library/recruiting/candidate-researcher/</loc>",
            content,
        )
        self.assertIn(
            "<loc>http://example.com/library/sales/outreach-agent/</loc>",
            content,
        )
