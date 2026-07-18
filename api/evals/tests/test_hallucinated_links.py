from collections import Counter, defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    extract_bare_link_like_destinations,
    extract_http_urls,
    provenance_failures,
)
from api.evals.execution import WaitForIdleContext
from api.evals.runner import _update_suite_state
from api.models import EvalRun, EvalSuiteRun
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

    def test_candidate_pair_uses_one_http_fixture_and_url_free_long_filler(self):
        cases = {
            case.context_size: case
            for case in HALLUCINATED_LINK_CASES
            if case.pattern.slug_root == "association_candidate_tool"
        }

        self.assertEqual(cases["short"].pattern.context_source, "tool")
        self.assertTrue(cases["short"].pattern.fixture_url)
        self.assertTrue(cases["short"].pattern.history_messages_are_outbound)
        self.assertEqual(len(cases["long"].context_messages()), 1)
        self.assertEqual(
            set(extract_http_urls(cases["long"].tool_payload())),
            set(extract_http_urls(cases["short"].tool_payload())),
        )

        config = ScenarioRegistry.get(cases["short"].slug)._mock_config(cases["short"])
        self.assertIn("http_request", config)
        self.assertIn("mcp_brightdata_search_engine", config)
        self.assertIn("mcp_brightdata_scrape_as_markdown", config)

    def test_bare_domain_paths_are_recorded_as_diagnostics_only(self):
        text = (
            "Profile: linkedin.com/in/alice-romero. "
            "Source: https://linkedin.com/in/ben-okafor and alice@example.com."
        )

        self.assertEqual(
            extract_bare_link_like_destinations(text),
            ("linkedin.com/in/alice-romero",),
        )

    def test_wait_for_idle_context_exposes_success_and_timeout_state(self):
        listener = MagicMock()
        listener.wait_for.return_value = {"payload": {"outstanding_tasks": 0}}
        with patch("api.evals.execution.AgentEventListener", return_value=listener):
            successful = WaitForIdleContext("agent-1", timeout=1)
            with successful:
                pass

        self.assertTrue(successful.idle)
        self.assertFalse(successful.timed_out)

        with patch("api.evals.execution.AgentEventListener"):
            timed_out = WaitForIdleContext("agent-2", timeout=0)
            with timed_out:
                pass

        self.assertFalse(timed_out.idle)
        self.assertTrue(timed_out.timed_out)

    def test_running_suite_has_no_finished_at_even_when_a_child_errored(self):
        finished = datetime(2026, 7, 18, tzinfo=timezone.utc)
        runs = [
            SimpleNamespace(status=EvalRun.Status.ERRORED, started_at=finished, finished_at=finished),
            SimpleNamespace(status=EvalRun.Status.RUNNING, started_at=finished, finished_at=None),
        ]
        suite = SimpleNamespace(
            runs=SimpleNamespace(all=lambda: runs),
            status=EvalSuiteRun.Status.RUNNING,
            started_at=None,
            finished_at=finished,
            save=MagicMock(),
        )
        manager = MagicMock()
        manager.select_related.return_value.prefetch_related.return_value.get.return_value = suite

        with patch("api.evals.runner.EvalSuiteRun.objects", manager), patch(
            "api.evals.runner.broadcast_suite_update"
        ):
            _update_suite_state("suite-1")

        self.assertEqual(suite.status, EvalSuiteRun.Status.RUNNING)
        self.assertIsNone(suite.finished_at)
