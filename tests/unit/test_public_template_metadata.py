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

    def test_long_description_uses_complete_comma_or_semicolon_clause(self):
        source = (
            "Research target accounts, summarize company fit and recent buying signals; "
            "prepare source-linked briefs for seller review, then add several additional "
            "details that would push the description beyond the preferred search length."
        )

        result = compose_meta_description(
            explicit_description="",
            description=source,
            tagline="",
            display_name="Account Research",
        )

        self.assertEqual(
            result,
            "Research target accounts, summarize company fit and recent buying signals; "
            "prepare source-linked briefs for seller review.",
        )

    def test_unicode_sentence_punctuation_is_a_complete_boundary(self):
        first_sentence = (
            "Analyse les chaînes concurrentes et prépare un rapport clair pour l’équipe。"
        )
        result = compose_meta_description(
            explicit_description="",
            description=(
                f"{first_sentence} Ajoute ensuite de nombreux détails supplémentaires "
                "qui dépasseraient la longueur recommandée pour les résultats de recherche."
            ),
            tagline="",
            display_name="YouTube Analytics",
        )

        self.assertEqual(result, first_sentence)

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

    def test_dangling_conjunction_uses_a_complete_fallback_source(self):
        result = compose_meta_description(
            explicit_description="",
            description="Research accounts, summarize fit, and",
            tagline="Research accounts and summarize fit for seller review.",
            display_name="Account Research",
        )

        self.assertEqual(
            result,
            "Research accounts and summarize fit for seller review.",
        )

    def test_dangling_preposition_uses_a_complete_fallback_source(self):
        result = compose_meta_description(
            explicit_description="",
            description="Prepare the team for",
            tagline="Prepare a concise daily brief for the team.",
            display_name="Daily Brief",
        )

        self.assertEqual(result, "Prepare a concise daily brief for the team.")

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

    def test_long_titles_preserve_meaningful_final_role_words(self):
        display_names = (
            "Day Trading Simulator & Portfolio Tracker",
            "Enterprise Market Research & Analysis Assistant",
            "Multi-Channel Prospect Warming & Lead Generator",
            "Sales Talent Sourcing & SEO Link Automator",
        )
        for display_name in display_names:
            with self.subTest(display_name=display_name):
                metadata = build_public_template_metadata(
                    _template(display_name=display_name)
                )

                self.assertIn(display_name, metadata.seo_title)
                self.assertTrue(metadata.seo_title.endswith("| Gobii"))
                self.assertNotIn("…", metadata.seo_title)

    def test_long_suffix_free_title_preserves_the_complete_display_name(self):
        display_name = (
            "International Enterprise Account Qualification and "
            "Revenue Intelligence Specialist"
        )
        metadata = build_public_template_metadata(
            _template(
                display_name=display_name,
                omit_ai_agent_template_title_suffix=True,
            )
        )

        self.assertGreater(len(metadata.seo_title), SEO_TITLE_MAX_LENGTH)
        self.assertTrue(metadata.seo_title.endswith("| Gobii"))
        self.assertEqual(metadata.social_title, display_name)
        self.assertIn(display_name, metadata.seo_title)

    def test_migrated_high_value_pages_have_complete_editorial_descriptions(self):
        expected_descriptions = {
            "account-research-ai-agent": (
                "Research target accounts, evaluate company fit and buying signals, and "
                "receive sales-ready briefs with source links and personalized outreach angles."
            ),
            "b2b-lead-research-agent": (
                "Find qualified B2B leads, evaluate company fit and buying signals, and "
                "receive a structured prospect list with sources, fit notes, and outreach angles."
            ),
            "tpl-f69de33885cf": (
                "Use this AI employee to identify and vet professionals across LinkedIn "
                "and Apollo, initiate outreach sequences, and log qualified candidates in your CRM."
            ),
            "tpl-2db8238181de": (
                "Use this AI employee to identify priority emails, synthesize key information, "
                "and add supporting research to a focused morning brief for the day ahead."
            ),
            "tpl-123d8d8489c7": (
                "Use this AI employee to find promotional opportunities, create platform-specific "
                "content, run social and SEO outreach, and maintain a placement log."
            ),
            "tpl-c0e7bc3a8b89": (
                "Use this AI employee to find energy startups and established firms seeking "
                "investment, then deliver structured fundraising data and visual market summaries."
            ),
            "tpl-62de76261e7a": (
                "Use this AI employee to map business architecture, automate project management, "
                "and maintain a central source of truth across multiple projects."
            ),
            "tpl-8a8d105a3a25": (
                "Use this AI employee to discover and vet talent, enrich professional profiles, "
                "and manage recruitment pipelines across multiple industries."
            ),
            "tpl-f5a569d2babb": (
                "Use this AI employee to monitor YouTube performance, analyze competitor channels "
                "and videos, and identify trends that grow views and engagement."
            ),
            "tpl-7b410745415f": (
                "Use this AI employee to simulate stock trades, monitor market news and momentum, "
                "and maintain a persistent record of paper-portfolio performance."
            ),
            "tpl-508ef3ad9f20": (
                "Use this AI employee to research prospects, warm them through LinkedIn and email, "
                "and hand engaged leads to sales after genuine multi-touch conversations."
            ),
            "tpl-3cc144f89d77": (
                "Use this AI employee to source sales candidates, enrich professional profiles, "
                "and expand SEO reach through large-scale link distribution."
            ),
        }

        descriptions = []
        for code, expected_description in expected_descriptions.items():
            with self.subTest(code=code):
                metadata = build_public_template_metadata(
                    _template(
                        code=code,
                        seo_meta_description=(
                            "A legacy description that ends with a dangling word and."
                        ),
                    )
                )
                self.assertEqual(metadata.description, expected_description)
                descriptions.append(metadata.description)

        self.assertEqual(len(descriptions), len(set(descriptions)))

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
