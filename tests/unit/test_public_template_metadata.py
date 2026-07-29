from types import SimpleNamespace

from django.test import SimpleTestCase, tag

from pages.public_template_metadata import (
    META_DESCRIPTION_MAX_LENGTH,
    SEO_TITLE_MAX_LENGTH,
    build_public_template_metadata,
    compose_meta_description,
    get_public_template_seo_override,
)


def _template(**overrides):
    values = {
        "code": "metadata-test-template",
        "display_name": "Account Research",
        "tagline": "Research target accounts and summarize useful sales signals.",
        "description": (
            "Research target accounts across public business sources and return "
            "source-backed summaries for sales teams."
        ),
        "seo_meta_description": "",
        "omit_ai_agent_template_title_suffix": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@tag("batch_public_templates")
class PublicTemplateMetadataTests(SimpleTestCase):
    def test_prefers_complete_explicit_description(self):
        explicit_description = (
            "Research target accounts, verify useful buying signals, and prepare "
            "source-backed briefs for sales teams."
        )

        result = compose_meta_description(
            explicit_description=explicit_description,
            description="A different fallback description.",
            tagline="A different fallback tagline.",
            display_name="Account Research",
        )

        self.assertEqual(result, explicit_description)

    def test_long_description_uses_complete_sentences_without_ellipsis(self):
        first_sentence = (
            "Research qualified accounts across reliable public business sources "
            "and return concise findings for a sales team's review."
        )
        source = (
            f"{first_sentence} "
            "Then enrich every record with several additional paragraphs that would "
            "push a mechanically truncated description far beyond the desired limit."
        )

        result = compose_meta_description(
            explicit_description="",
            description=source,
            tagline="",
            display_name="Account Research",
        )

        self.assertEqual(result, first_sentence)
        self.assertLessEqual(len(result), META_DESCRIPTION_MAX_LENGTH)
        self.assertFalse(result.endswith(("...", "…")))

    def test_single_long_sentence_stops_at_a_word_boundary(self):
        source = (
            "Research renewable energy markets across policy announcements company "
            "reports project filings investment activity technology changes and "
            "regional demand signals before preparing a structured report for review"
        )

        result = compose_meta_description(
            explicit_description=source,
            description="",
            tagline="",
            display_name="Renewable Energy Analyst",
        )

        self.assertLessEqual(len(result), META_DESCRIPTION_MAX_LENGTH)
        self.assertTrue(result.endswith("."))
        self.assertIn(result[:-1].split()[-1], source.split())
        self.assertFalse(result.endswith(("...", "…")))

    def test_unicode_and_html_entities_remain_valid_plain_text(self):
        result = compose_meta_description(
            explicit_description=(
                "Research R&amp;D &lt;signals&gt; for café teams and return "
                "source-backed recommendations."
            ),
            description="",
            tagline="",
            display_name="R&D Research",
        )

        self.assertEqual(
            result,
            "Research R&D <signals> for café teams and return source-backed recommendations.",
        )

    def test_missing_copy_produces_a_readable_fallback(self):
        result = compose_meta_description(
            explicit_description="",
            description="",
            tagline="",
            display_name="Project Coordinator",
        )

        self.assertEqual(
            result,
            "Create a Project Coordinator AI employee from this reusable Gobii "
            "template and customize it for your workflow.",
        )

    def test_missing_display_name_does_not_create_empty_metadata(self):
        metadata = build_public_template_metadata(
            _template(
                display_name="",
                tagline="",
                description="",
                seo_meta_description="",
            )
        )

        self.assertEqual(metadata.heading, "Reusable Role AI Employee")
        self.assertTrue(metadata.seo_title)
        self.assertTrue(metadata.description)
        self.assertLessEqual(len(metadata.seo_title), SEO_TITLE_MAX_LENGTH)
        self.assertLessEqual(
            len(metadata.description),
            META_DESCRIPTION_MAX_LENGTH,
        )

    def test_terminal_agent_role_uses_employee_first_heading(self):
        metadata = build_public_template_metadata(
            _template(display_name="Outreach Agent")
        )

        self.assertEqual(metadata.heading, "Outreach AI Employee")
        self.assertEqual(
            metadata.social_title,
            "Outreach AI Employee Template",
        )

    def test_exact_description_boundary_is_not_modified(self):
        source = "A" * META_DESCRIPTION_MAX_LENGTH

        result = compose_meta_description(
            explicit_description=source,
            description="",
            tagline="",
            display_name="Boundary Test",
        )

        self.assertEqual(result, source)

    def test_long_title_is_shortened_without_cutting_a_word(self):
        metadata = build_public_template_metadata(
            _template(
                display_name=(
                    "International Enterprise Account Qualification and "
                    "Revenue Intelligence Specialist"
                )
            )
        )

        self.assertLessEqual(len(metadata.seo_title), SEO_TITLE_MAX_LENGTH)
        self.assertTrue(metadata.seo_title.endswith("AI Employee | Gobii"))
        self.assertNotIn("…", metadata.seo_title)

    def test_long_suffix_free_title_is_still_bounded(self):
        metadata = build_public_template_metadata(
            _template(
                display_name=(
                    "International Enterprise Account Qualification and "
                    "Revenue Intelligence Specialist"
                ),
                omit_ai_agent_template_title_suffix=True,
            )
        )

        self.assertLessEqual(len(metadata.seo_title), SEO_TITLE_MAX_LENGTH)
        self.assertTrue(metadata.seo_title.endswith("| Gobii"))
        self.assertEqual(
            metadata.social_title,
            "International Enterprise Account Qualification and Revenue "
            "Intelligence Specialist",
        )

    def test_known_collision_pages_have_unique_titles_headings_and_descriptions(self):
        codes = (
            "tpl-f2c5bb1cdb34",
            "tpl-2a3ec836a1cd",
            "tpl-613e6c63700d",
            "tpl-2e73efd36bee",
            "tpl-c1f7eff8a2f5",
            "tpl-12203bdb9209",
        )
        metadata = [
            build_public_template_metadata(
                _template(
                    code=code,
                    display_name="Shared Display Name",
                    description="Shared description.",
                )
            )
            for code in codes
        ]

        self.assertEqual(len({item.heading for item in metadata}), len(codes))
        self.assertEqual(len({item.seo_title for item in metadata}), len(codes))
        self.assertEqual(len({item.description for item in metadata}), len(codes))
        self.assertTrue(
            all(
                len(item.seo_title) <= SEO_TITLE_MAX_LENGTH
                and len(item.description) <= META_DESCRIPTION_MAX_LENGTH
                for item in metadata
            )
        )
        outputs = [
            get_public_template_seo_override(_template(code=code)).example_outputs
            for code in codes
        ]
        self.assertEqual(len(set(outputs)), len(codes))
