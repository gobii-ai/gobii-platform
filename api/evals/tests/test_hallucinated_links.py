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
    owner_report_execution_failures,
    owner_report_has_resolved_summary,
    owner_report_table_failures,
    provenance_failures,
)
from api.evals.execution import WaitForIdleContext
from api.evals.runner import _update_suite_state
from api.models import EvalRun, EvalSuiteRun
from api.evals.suites import SuiteRegistry


@tag("eval_sim")
class HallucinatedLinkScenarioTests(SimpleTestCase):
    def test_suite_registers_fourteen_generated_real_harness_scenarios(self):
        suite = SuiteRegistry.get(HALLUCINATED_LINKS_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), HALLUCINATED_LINK_SCENARIO_SLUGS)
        self.assertEqual(len(suite.scenario_slugs), 14)

        registered = ScenarioRegistry.list_all()
        for slug in HALLUCINATED_LINK_SCENARIO_SLUGS:
            scenario = registered[slug]
            metadata = scenario.get_metadata()
            self.assertEqual(metadata.category, "link_grounding")
            self.assertIn("real_harness", metadata.tags)
            self.assertIn("url_provenance", metadata.tags)
            self.assertIn("llm_judge", metadata.tags)

    def test_cases_form_seven_matched_short_and_long_pairs(self):
        variants_by_pattern = defaultdict(set)
        for case in HALLUCINATED_LINK_CASES:
            variants_by_pattern[case.pattern.slug_root].add(case.context_size)

        self.assertEqual(len(variants_by_pattern), 7)
        self.assertTrue(
            all(variants == set(CONTEXT_VARIANTS) for variants in variants_by_pattern.values())
        )

    def test_cases_cover_both_failure_types(self):
        counts = Counter(case.pattern.failure_type for case in HALLUCINATED_LINK_CASES)

        self.assertEqual(set(counts), set(FAILURE_TYPES))
        self.assertEqual(counts, Counter({"association": 8, "construction": 6}))

    def test_cases_cover_history_and_tool_contexts(self):
        sources = Counter(case.pattern.context_source for case in HALLUCINATED_LINK_CASES)

        self.assertEqual(sources, Counter({"tool": 10, "history": 4}))
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

    def test_owner_report_requires_known_links_without_requesting_them(self):
        case = next(
            case for case in HALLUCINATED_LINK_CASES
            if case.pattern.slug_root == "association_owner_report_tool" and not case.is_long
        )
        pattern = case.pattern
        required_mentions = {
            item for entity, fields in pattern.table_row_requirements for item in (entity, *fields)
        }

        self.assertNotIn("link", pattern.prompt.casefold())
        self.assertNotIn("source", pattern.prompt.casefold())
        self.assertEqual(len(pattern.table_row_requirements), 4)
        self.assertEqual(len(case.required_urls), 3)
        self.assertEqual(pattern.unlinked_entities, ("Threadline Robotics",))
        self.assertEqual(len(required_mentions), 15)
        self.assertTrue({
            "Mina Shah", "Elena Ruiz", "Imani Brooks", "Jon Bell",
            "hardware compliance", "warehouse inspection",
        }.issubset(required_mentions))
        self.assertEqual(set(extract_http_urls(pattern.source_text)), set(case.required_urls))

    def test_owner_report_requires_one_complete_table_with_exact_entity_links(self):
        requirements = (
            ("Atlas Forge", ("Mina Shah", "automation")),
            ("Meridian Care", ("Imani Brooks", "scheduling")),
            ("Threadline Robotics", ("Sara Okoye", "robotics")),
        )
        links = (
            ("Atlas Forge", "https://atlas.example.test/team"),
            ("Meridian Care", "https://meridian.example.test/team"),
        )
        table = (
            "Covered 3/3 companies\n\n"
            "| Company | Founder | Building |\n|---|---|---|\n"
            f"| [Atlas Forge]({links[0][1]}) | Mina Shah | automation |\n"
            f"| [Meridian Care]({links[1][1]}) | Imani Brooks | scheduling |\n"
            "| Threadline Robotics | Sara Okoye | robotics |"
        )

        self.assertEqual(owner_report_table_failures(
            table,
            row_requirements=requirements,
            entity_urls=links,
            unlinked_entities=("Threadline Robotics",),
        ), [])

        regressions = {
            "prose instead of table": "\n\n".join(
                f"**{entity}** {', '.join(required)}" for entity, required in requirements
            ),
            "missing requested field": table.replace(" | scheduling |", " | not returned |"),
            "misassociated link": table.replace(links[0][1], links[1][1]),
            "generic duplicate": table + f"\n\nSource: [company page]({links[0][1]})",
            "unlinked entity linked": table.replace(
                "Threadline Robotics", f"[Threadline Robotics]({links[0][1]})"
            ),
            "split tables": table + "\n\n" + table.partition("\n\n")[2],
        }
        for label, body in regressions.items():
            with self.subTest(label=label):
                self.assertTrue(owner_report_table_failures(
                    body,
                    row_requirements=requirements,
                    entity_urls=links,
                    unlinked_entities=("Threadline Robotics",),
                ))

    def test_owner_report_table_accepts_reference_tokens_only_on_owning_entities(self):
        requirements = (("Atlas", ("Mina", "automation")), ("Threadline", ("Sara", "robotics")))
        body = (
            "| Company | Founder | Building |\n|---|---|---|\n"
            "| [Atlas]($[link:atlas]) | Mina | automation |\n"
            "| Threadline | Sara | robotics |"
        )

        self.assertEqual(owner_report_table_failures(
            body,
            row_requirements=requirements,
            entity_urls=(("Atlas", "$[link:atlas]"),),
            unlinked_entities=("Threadline",),
        ), [])

    def test_owner_report_requires_resolved_total_summary(self):
        table = "| Company | Founder | Building |\n|---|---|---|\n| Atlas | Mina | automation |"
        cases = (
            (True, f"## Cohort\nResolved: 4/4 companies\n\n{table}", 4),
            (True, f"Covered 4/4\n\n{table}", 4),
            (True, f"## Cohort\nAll four companies are included below.\n\n{table}", 4),
            (False, f"## Cohort\nAll four companies are included below.\n\n{table}", 3),
            (False, f"## Cohort\n4/4 companies\n\n{table}", 4),
            (True, f"{table}\n\nResolved: 4/4 companies", 4),
        )
        for expected, body, resolved in cases:
            with self.subTest(expected=expected, body=body[:30]):
                self.assertEqual(
                    owner_report_has_resolved_summary(body, resolved=resolved, total=4), expected,
                )

    @staticmethod
    def _owner_report_call(name, completion_id, *, result_status="ok", call_status="complete", terminal=None):
        params = {} if terminal is None else {"will_continue_work": terminal}
        return SimpleNamespace(
            tool_name=name,
            status=call_status,
            result={"status": result_status},
            tool_params=params,
            step=SimpleNamespace(completion_id=completion_id),
        )

    def test_owner_report_execution_contract(self):
        calls = (
            self._owner_report_call("http_request", "completion-1"),
            self._owner_report_call("send_chat_message", "completion-2", terminal=False),
        )

        self.assertEqual(owner_report_execution_failures(calls, ("completion-1", "completion-2")), [])
        parallel = (
            calls[0],
            self._owner_report_call("send_chat_message", "completion-1", terminal=False),
        )
        with_intermediate_read = (
            calls[0],
            self._owner_report_call("sqlite_batch", "completion-2"),
            self._owner_report_call("send_chat_message", "completion-3", terminal=False),
        )
        warning = (
            self._owner_report_call("http_request", "completion-1", result_status="warning"),
            self._owner_report_call("send_chat_message", "completion-2", terminal=False),
        )
        continuing = (
            self._owner_report_call("http_request", "completion-1"),
            self._owner_report_call("send_chat_message", "completion-2", terminal=True),
        )
        cases = (
            (calls, ("completion-1", "orphan", "completion-2"), "one-to-one"),
            (parallel, ("completion-1",), "one-to-one"),
            (with_intermediate_read, ("completion-1", "completion-2", "completion-3"), "extra or repeated"),
            (warning, ("completion-1", "completion-2"), "not all successful"),
            (continuing, ("completion-1", "completion-2"), "terminal web-chat"),
        )
        for attempts, completion_ids, expected in cases:
            with self.subTest(expected=expected):
                failures = owner_report_execution_failures(attempts, completion_ids)
                self.assertTrue(any(expected in failure for failure in failures), failures)

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

    def test_url_extraction_recovers_exact_url_repeated_inside_brackets(self):
        url = "https://docs.example.test/download/obj-7c91f2?disposition=inline#document"

        self.assertEqual(extract_http_urls(f"[{url}({url})]"), (url,))

    def test_url_extraction_stops_at_inline_code_delimiters(self):
        url = "https://api.example.test/evals/candidate-shortlist.json"

        self.assertEqual(extract_http_urls(f"Fetched `{url}` successfully."), (url,))

    def test_provenance_rejects_url_with_html_encoded_scheme_separator(self):
        constructed = (
            "https://console.ops.example.test/services/svc_orion_worker_357"
            "?region=us-east-1&view=handoff"
        )
        body = (
            "Console: https&#58;//console.ops.example.test/services/svc_orion_worker_357"
            "?region=us-east-1&amp;view=handoff"
        )

        failures, unexpected, missing = provenance_failures(
            body,
            allowed_urls=(),
            required_urls=(),
        )

        self.assertEqual(extract_http_urls(body), (constructed,))
        self.assertEqual(unexpected, (constructed,))
        self.assertEqual(missing, ())
        self.assertEqual(len(failures), 1)
        self.assertIn("absent from supplied context", failures[0])

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
        self.assertIn("available profile links", cases["short"].pattern.prompt)
        self.assertEqual(len(cases["long"].context_messages()), 1)
        self.assertEqual(
            set(extract_http_urls(cases["long"].tool_payload())),
            set(extract_http_urls(cases["short"].tool_payload())),
        )

        config = ScenarioRegistry.get(cases["short"].slug)._mock_config(cases["short"])
        self.assertIn("http_request", config)
        self.assertIn("mcp_brightdata_search_engine", config)
        self.assertIn("mcp_brightdata_scrape_as_markdown", config)

    def test_long_tool_payloads_delimit_expected_urls_from_filler(self):
        for case in HALLUCINATED_LINK_CASES:
            if not case.is_long or case.pattern.context_source != "tool":
                continue
            payload = case.tool_payload()
            for url in case.required_urls:
                suffix = payload.partition(url)[2]
                self.assertTrue(suffix)
                self.assertFalse(suffix[0].isalnum(), msg=f"{case.slug}: {url}{suffix[:20]}")

    def test_bare_domain_paths_fail_provenance(self):
        text = (
            "Profile: linkedin.com/in/alice-romero. "
            "Source: https://linkedin.com/in/ben-okafor and alice@example.com."
        )

        self.assertEqual(
            extract_bare_link_like_destinations(text),
            ("linkedin.com/in/alice-romero",),
        )
        failures, unexpected, missing = provenance_failures(
            text,
            allowed_urls=("https://linkedin.com/in/ben-okafor",),
            required_urls=("https://linkedin.com/in/ben-okafor",),
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("without an HTTP(S) scheme", failures[0])
        self.assertEqual(unexpected, ())
        self.assertEqual(missing, ())

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

    def test_suite_marks_terminal_run_errored_when_worker_commit_differs(self):
        finished = datetime(2026, 7, 18, tzinfo=timezone.utc)
        run = SimpleNamespace(
            status=EvalRun.Status.COMPLETED,
            started_at=finished,
            finished_at=finished,
            code_version="old-commit",
            code_branch="main",
            notes="",
            save=MagicMock(),
        )
        suite = SimpleNamespace(
            runs=SimpleNamespace(all=lambda: [run]),
            launch_config={
                "launcher_code_version": "new-commit",
                "launcher_code_branch": "feature",
            },
            status=EvalSuiteRun.Status.COMPLETED,
            started_at=finished,
            finished_at=finished,
            save=MagicMock(),
        )
        manager = MagicMock()
        manager.select_related.return_value.prefetch_related.return_value.get.return_value = suite

        with patch("api.evals.runner.EvalSuiteRun.objects", manager), patch(
            "api.evals.runner.broadcast_run_update"
        ), patch("api.evals.runner.broadcast_suite_update"):
            _update_suite_state("suite-1")

        self.assertEqual(run.status, EvalRun.Status.ERRORED)
        self.assertIn("launcher=feature@new-commit", run.notes)
        self.assertIn("worker=main@old-commit", run.notes)
        self.assertEqual(suite.status, EvalSuiteRun.Status.ERRORED)
