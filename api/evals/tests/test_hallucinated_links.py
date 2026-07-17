from collections import Counter, defaultdict

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.hallucinated_links import (
    CONTEXT_VARIANTS,
    FAILURE_TYPES,
    HALLUCINATED_LINK_CASES,
    HALLUCINATED_LINK_SCENARIO_SLUGS,
    HALLUCINATED_LINKS_SUITE_SLUG,
    LINK_GROUNDING_PATTERNS,
    LONG_CONTEXT_MIN_CHARS,
    extract_http_urls,
    provenance_failures,
)
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class HallucinatedLinkScenarioTests(SimpleTestCase):
    def test_suite_registers_twelve_generated_real_harness_scenarios(self):
        suite = SuiteRegistry.get(HALLUCINATED_LINKS_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), HALLUCINATED_LINK_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 12)

        registered = ScenarioRegistry.list_all()
        for slug in HALLUCINATED_LINK_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "link_grounding")
            self.assertIn("real_harness", metadata.tags)
            self.assertIn("url_provenance", metadata.tags)
            self.assertIn("llm_judge", metadata.tags)

    def test_cases_form_six_matched_short_and_long_pairs(self):
        variants_by_pattern = defaultdict(set)
        for case in HALLUCINATED_LINK_CASES:
            variants_by_pattern[case.pattern.slug_root].add(case.context_size)

        self.assertEqual(len(variants_by_pattern), 6)
        self.assertTrue(
            all(variants == set(CONTEXT_VARIANTS) for variants in variants_by_pattern.values())
        )

    def test_cases_split_evenly_between_failure_types(self):
        counts = Counter(case.pattern.failure_type for case in HALLUCINATED_LINK_CASES)

        self.assertEqual(set(counts), set(FAILURE_TYPES))
        self.assertEqual(counts, Counter({"association": 6, "construction": 6}))

    def test_cases_cover_history_and_tool_contexts(self):
        sources = Counter(case.pattern.context_source for case in HALLUCINATED_LINK_CASES)

        self.assertEqual(sources, Counter({"tool": 8, "history": 4}))
        self.assertEqual(
            {pattern.relevant_position for pattern in LINK_GROUNDING_PATTERNS},
            {"early", "middle", "late"},
        )

    def test_long_variants_exceed_minimum_size_without_changing_ground_truth(self):
        cases_by_pattern = defaultdict(dict)
        for case in HALLUCINATED_LINK_CASES:
            cases_by_pattern[case.pattern.slug_root][case.context_size] = case

        for variants in cases_by_pattern.values():
            short = variants["short"]
            long = variants["long"]
            self.assertLess(short.context_character_count(), LONG_CONTEXT_MIN_CHARS)
            self.assertGreaterEqual(long.context_character_count(), LONG_CONTEXT_MIN_CHARS)
            self.assertEqual(short.pattern.prompt, long.pattern.prompt)
            self.assertEqual(short.pattern.source_text, long.pattern.source_text)
            self.assertEqual(short.required_urls, long.required_urls)
            self.assertEqual(short.pattern.unlinked_entities, long.pattern.unlinked_entities)

    def test_eval_prompts_do_not_leak_link_grounding_rubric(self):
        forbidden_terms = (
            "hallucinat",
            "fabricat",
            "invent",
            "exact copy",
            "copy the url",
            "url accuracy",
            "grading",
            "rubric",
        )

        for pattern in LINK_GROUNDING_PATTERNS:
            prompt = pattern.prompt.casefold()
            for term in forbidden_terms:
                self.assertNotIn(term, prompt)

    def test_url_extraction_handles_plain_markdown_and_html_urls(self):
        body = (
            "Plain: https://plain.example.test/report.pdf?download=1#page=2.\n"
            "Markdown: [Profile](https://profiles.example.test/mira_(data)?view=public&amp;tab=work).\n"
            '<a href="https://files.example.test/board.xlsx?download=1&amp;sheet=risk">Risk</a>\n'
            "Bold Markdown: **[Candidate](https://profiles.example.test/candidate-42)**"
        )

        self.assertEqual(
            extract_http_urls(body),
            (
                "https://plain.example.test/report.pdf?download=1#page=2",
                "https://profiles.example.test/mira_(data)?view=public&tab=work",
                "https://files.example.test/board.xlsx?download=1&sheet=risk",
                "https://profiles.example.test/candidate-42",
            ),
        )

    def test_url_extraction_deduplicates_without_changing_query_or_fragment(self):
        url = "https://status.example.test/services/orion?region=us-east-1#history"

        self.assertEqual(extract_http_urls(f"{url}\n[{url}]({url})"), (url,))

    def test_url_extraction_stops_at_inline_code_delimiters(self):
        url = "https://api.example.test/evals/candidate-shortlist.json"

        self.assertEqual(extract_http_urls(f"Fetched `{url}` successfully."), (url,))

    def test_provenance_accepts_only_supplied_required_urls(self):
        required = (
            "https://profiles.example.test/alice?view=public",
            "https://profiles.example.test/ben#summary",
        )
        body = "Alice: https://profiles.example.test/alice?view=public\nBen: https://profiles.example.test/ben#summary"

        failures, unexpected, missing = provenance_failures(
            body,
            allowed_urls=required,
            required_urls=required,
        )

        self.assertEqual(failures, [])
        self.assertEqual(unexpected, ())
        self.assertEqual(missing, ())

    def test_provenance_rejects_constructed_and_missing_urls(self):
        alice = "https://profiles.example.test/alice"
        ben = "https://profiles.example.test/ben"
        constructed = "https://profiles.example.test/chloe"

        failures, unexpected, missing = provenance_failures(
            f"Alice: {alice}\nChloe: {constructed}",
            allowed_urls=(alice, ben),
            required_urls=(alice, ben),
        )

        self.assertEqual(unexpected, (constructed,))
        self.assertEqual(missing, (ben,))
        self.assertEqual(len(failures), 2)

    def test_fixture_endpoint_is_allowed_but_not_required_in_output(self):
        case = next(
            case
            for case in HALLUCINATED_LINK_CASES
            if case.pattern.context_source == "tool"
            and case.context_size == "short"
            and case.pattern.fixture_url
        )

        self.assertIn(case.pattern.fixture_url, case.allowed_urls)
        self.assertNotIn(case.pattern.fixture_url, case.required_urls)
