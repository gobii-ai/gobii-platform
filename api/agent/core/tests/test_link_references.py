import json
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase, override_settings, tag
from django.utils import timezone as django_timezone

from api.agent.core.link_references import (
    LinkReferenceResolutionError,
    extract_http_urls,
    is_source_bearing_tool,
    pair_prompt_urls,
    resolve_link_reference_params,
    resolve_link_references,
    resolve_link_references_for_display,
    rewrite_prompt_urls,
)
from api.agent.core.event_processing import (
    _execute_tool_call_runtime,
    _finalize_tool_batch,
    _PreparedToolExecution,
    _prepare_tool_batch,
    _ToolExecutionOutcome,
)
from api.agent.core.prompt_context import _get_system_instruction, build_prompt_context
from api.agent.core.tool_results import ToolCallResultRecord, prepare_tool_results_for_prompt
from api.agent.tools.agent_variables import (
    clear_variables,
    set_agent_variable,
    substitute_variables_with_filespace,
)
from api.agent.tools.email_sender import execute_send_email
from api.agent.tools.create_csv import execute_create_csv
from api.agent.tools.create_file import execute_create_file
from api.agent.tools.create_pdf import execute_create_pdf
from api.agent.tools.http_request import get_http_request_tool
from api.agent.tools.peer_dm import execute_send_agent_message
from api.agent.tools.send_discord_message import execute_send_discord_message
from api.agent.tools.sms_sender import execute_send_sms
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsSnapshot,
    PersistentAgentCommsEndpoint,
    PersistentAgentLinkReference,
    PersistentAgentMessage,
    PersistentAgentToolCall,
)
from util.text_sanitizer import strip_markdown_for_sms


@tag("batch_event_processing")
class LinkReferenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="link-references@example.com",
            email="link-references@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="LinkReferencesBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Link References Agent",
            charter="Summarize sourced records.",
            browser_use_agent=browser_agent,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        other_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="OtherLinkReferencesBA")
        self.other_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Other Link References Agent",
            charter="Handle other records.",
            browser_use_agent=other_browser_agent,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        clear_variables()

    def tearDown(self):
        clear_variables()

    def test_extracts_markdown_html_and_plain_urls_exactly(self):
        text = (
            "[One](https://one.example.test/a?x=1#frag) "
            "<a href='https://two.example.test/b'>Two</a> "
            "https://three.example.test/c."
        )
        self.assertEqual(
            extract_http_urls(text),
            (
                "https://one.example.test/a?x=1#frag",
                "https://two.example.test/b",
                "https://three.example.test/c",
            ),
        )

    def test_html_escaped_query_preserves_only_the_original_suffix(self):
        raw_url = "https://items.example.test/42?first=one&amp;second=two#details"

        rendered = rewrite_prompt_urls(
            f"Open {raw_url}.",
            self.agent,
            create=True,
        )

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent)
        self.assertEqual(reference.url, "https://items.example.test/42?first=one&second=two#details")
        self.assertEqual(rendered, f"Open $[link:{reference.public_id}].")

    def test_display_resolution_recurses_without_json_round_trip(self):
        url = 'https://items.example.test/search?q="quoted"&path=C:\\reports'
        reference = PersistentAgentLinkReference.objects.create(
            agent=self.agent,
            url=url,
        )
        token = f"$[link:{reference.public_id}]"
        value = {
            "nested": [f"Open {token}", {"url": token}],
            "count": 2,
        }

        self.assertEqual(
            resolve_link_references_for_display(value, self.agent),
            {
                "nested": [f"Open {url}", {"url": url}],
                "count": 2,
            },
        )

    def test_registration_deduplicates_and_preserves_exact_url(self):
        url = "https://items.example.test/42/path?view=full&region=west#details"
        first = rewrite_prompt_urls(
            f"[Item]({url})",
            self.agent,
            create=True,
        )
        second = rewrite_prompt_urls(
            f"<a href='{url}'>Item</a>",
            self.agent,
            create=True,
        )

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent)
        token = f"$[link:{reference.public_id}]"
        self.assertEqual(reference.url, url)
        self.assertRegex(reference.public_id, r"^L[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{16}$")
        self.assertEqual(PersistentAgentLinkReference.objects.count(), 1)
        self.assertEqual(first, f"[Item]({token})")
        self.assertEqual(second, f"<a href='{token}'>Item</a>")

    def test_pairing_keeps_similar_raw_urls_attached_to_distinct_references(self):
        first_url = "https://items.example.test/id/1?view=full#details"
        second_url = "https://items.example.test/id/2?view=full#details"

        rendered = pair_prompt_urls(
            f"First: {first_url}\nSecond: {second_url}",
            self.agent,
            create=True,
        )

        references = {
            reference.url: f"$[link:{reference.public_id}]"
            for reference in PersistentAgentLinkReference.objects.filter(agent=self.agent)
        }
        self.assertEqual(len(references), 2)
        self.assertIn(f"{first_url} [link_ref: {references[first_url]}]", rendered)
        self.assertIn(f"{second_url} [link_ref: {references[second_url]}]", rendered)
        self.assertEqual(pair_prompt_urls(rendered, self.agent, create=False), rendered)

    def test_pairing_keeps_markdown_destination_valid(self):
        url = "https://items.example.test/id/7?view=full#details"

        rendered = pair_prompt_urls(f"[Item]({url}).", self.agent, create=True)

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent)
        token = f"$[link:{reference.public_id}]"
        self.assertEqual(rendered, f"[Item]({url}) [link_ref: {token}].")
        self.assertEqual(pair_prompt_urls(rendered, self.agent, create=False), rendered)

    def test_pairing_keeps_html_href_valid(self):
        url = "https://items.example.test/id/7?view=full&amp;region=west#details"
        for quote in ('"', "'", ""):
            with self.subTest(quote=quote or "unquoted"):
                source = f"<a class='item' href={quote}{url}{quote}>Item</a>."
                rendered = pair_prompt_urls(source, self.agent, create=True)

                reference = PersistentAgentLinkReference.objects.get(agent=self.agent)
                token = f"$[link:{reference.public_id}]"
                expected = f"<a class='item' href={quote}{url}{quote}> [link_ref: {token}]Item</a>."
                self.assertEqual(rendered, expected)
                self.assertEqual(pair_prompt_urls(rendered, self.agent, create=False), rendered)

    def test_lookup_only_does_not_create_provenance(self):
        url = "https://derived.example.test/records/9"
        self.assertEqual(
            rewrite_prompt_urls(url, self.agent, create=False),
            url,
        )
        self.assertEqual(pair_prompt_urls(url, self.agent, create=False), url)
        self.assertFalse(PersistentAgentLinkReference.objects.exists())

        registered = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
        )
        self.assertEqual(rewrite_prompt_urls(url, self.agent, create=False), registered)
        self.assertIn(registered, pair_prompt_urls(url, self.agent, create=False))

    def test_reference_resolves_across_calls_in_markdown_html_and_plain_text(self):
        url = "https://profiles.example.test/avery?campaign=q3#experience"
        token = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
        )

        text = f"[Profile]({token}) <a href='{token}'>HTML</a> Plain: {token}"
        self.assertEqual(
            substitute_variables_with_filespace(text, self.agent),
            f"[Profile]({url}) <a href='{url}'>HTML</a> Plain: {url}",
        )
        self.assertEqual(
            strip_markdown_for_sms(
                substitute_variables_with_filespace(f"[Profile]({token})", self.agent)
            ),
            f"Profile ({url})",
        )

    def test_tool_params_resolve_only_literal_complete_reference_values_recursively(self):
        url = "https://profiles.example.test/avery?campaign=q3#experience"
        token = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
        )
        reference_id = token.removeprefix("$[link:").removesuffix("]")
        params = {
            "url": token,
            "related": [f"https://gobii.example.test/api/links/{reference_id}"],
            "query": f"Compare {token} with the other result",
            "file": "$[/reports/summary.pdf]",
        }

        self.assertEqual(
            resolve_link_reference_params(params, self.agent),
            {
                "url": url,
                "related": [f"https://gobii.example.test/api/links/{reference_id}"],
                "query": f"Compare {token} with the other result",
                "file": "$[/reports/summary.pdf]",
            },
        )

    def test_tool_runtime_resolves_reference_before_eval_mock_matching(self):
        url = "https://profiles.example.test/avery?view=full"
        token = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
        )
        mock_result = {"status": "ok", "profile": "Avery"}
        budget_ctx = SimpleNamespace(
            mock_config={
                "mcp_profiles": {
                    "rules": [{"param_contains": {"url": url}, "result": mock_result}],
                    "default": {"status": "error"},
                }
            }
        )

        result, _ = _execute_tool_call_runtime(
            self.agent,
            tool_name="mcp_profiles",
            exec_params={"url": token},
            budget_ctx=budget_ctx,
            eval_run_id="eval-link-reference",
        )

        self.assertEqual(result, mock_result)

    def test_http_request_body_resolves_embedded_references_before_execution(self):
        url = "https://profiles.example.test/avery,chen?view=full#bio"
        token = rewrite_prompt_urls(url, self.agent, create=True)

        self.assertEqual(
            resolve_link_reference_params(
                {"url": "https://sheets.googleapis.com/v4/spreadsheets/123/values/A1", "body": json.dumps({"values": [[token]]})},
                self.agent,
                tool_name="http_request",
            ),
            {"url": "https://sheets.googleapis.com/v4/spreadsheets/123/values/A1", "body": json.dumps({"values": [[url]]})},
        )

    def test_runtime_rejects_references_in_unsupported_tool_fields_before_execution(self):
        token = rewrite_prompt_urls(
            "https://profiles.example.test/avery?view=full",
            self.agent,
            create=True,
        )
        cases = (
            ("create_image", {"prompt": f"Render {token}", "file_path": "/exports/a.png"}, "create_image.prompt"),
            ("create_video", {"prompt": token, "file_path": "/exports/a.mp4"}, "create_video.prompt"),
            ("sqlite_batch", {"queries": [{"sql": f"SELECT '{token}'"}]}, "sqlite_batch.queries[0].sql"),
            ("apply_patch", {"patch": f"+ {token}"}, "apply_patch.patch"),
            ("send_email", {"subject": token, "mobile_first_html": "Body"}, "send_email.subject"),
            ("send_webhook_event", {"payload": {"item": {"url": token}}}, "send_webhook_event.payload.item.url"),
        )

        with patch("api.agent.core.event_processing.execute_enabled_tool") as enabled, patch(
            "api.agent.core.event_processing.execute_apply_patch"
        ) as apply_patch_mock, patch(
            "api.agent.core.event_processing.execute_send_email"
        ) as email_mock:
            for tool_name, params, path in cases:
                with self.subTest(tool_name=tool_name):
                    result, _ = _execute_tool_call_runtime(
                        self.agent,
                        tool_name=tool_name,
                        exec_params=params,
                        budget_ctx=None,
                        eval_run_id=None,
                    )
                    self.assertEqual(result["status"], "error")
                    self.assertTrue(result["retryable"])
                    self.assertIn(path, result["message"])
                    self.assertIn("Query not executed", result["message"])
                    if tool_name == "sqlite_batch":
                        self.assertIn("Derive raw values/URLs inside INSERT ... SELECT", result["message"])
                        self.assertIn("replace tokens with literals", result["message"])

        enabled.assert_not_called()
        apply_patch_mock.assert_not_called()
        email_mock.assert_not_called()

    def test_runtime_allows_embedded_references_only_in_supported_content_fields(self):
        token = rewrite_prompt_urls(
            "https://profiles.example.test/avery",
            self.agent,
            create=True,
        )
        cases = (
            ("create_csv", {"csv_text": f"name,url\nAvery,{token}", "file_path": "/exports/a.csv"}),
            ("create_file", {"content": f"Avery: {token}", "mime_type": "text/markdown", "file_path": "/exports/a.md"}),
            ("create_pdf", {"html": f"<a href='{token}'>Avery</a>", "file_path": "/exports/a.pdf"}),
            ("send_chat_message", {"body": f"[Avery]({token})", "will_continue_work": False}),
        )

        for tool_name, params in cases:
            with self.subTest(tool_name=tool_name):
                self.assertEqual(
                    resolve_link_reference_params(params, self.agent, tool_name=tool_name),
                    params,
                )

        with self.assertRaises(LinkReferenceResolutionError) as raised:
            resolve_link_reference_params(
                {"content": token, "mime_type": "text/x-python"},
                self.agent,
                tool_name="create_file",
            )
        self.assertIn("create_file.content", str(raised.exception))

    def test_rejects_references_concatenated_into_urls(self):
        token = rewrite_prompt_urls(
            "https://profiles.example.test/avery",
            self.agent,
            create=True,
        )
        public_id = token.removeprefix("$[link:").removesuffix("]")

        for body in (
            f"[Profile](/{token})",
            f"[Profile](https://api.example.test/profiles/{token})",
            f"[Profile]({token}/details)",
        ):
            with self.subTest(body=body), self.assertRaises(LinkReferenceResolutionError):
                resolve_link_references(body, self.agent)

        raw_url = f"https://api.example.test/profiles/{public_id}"
        self.assertEqual(resolve_link_references(f"[Profile]({raw_url})", self.agent), f"[Profile]({raw_url})")
        foreign = "https://api.example.test/profiles/L0000000000000000"
        self.assertEqual(resolve_link_references(foreign, self.agent), foreign)

    def test_repairs_reference_used_as_markdown_label(self):
        url = "https://files.example.test/board-pack.pdf"
        token = rewrite_prompt_urls(url, self.agent, create=True)

        for destination in ("http://example.com", "$[link:L0000000000000000]"):
            with self.subTest(destination=destination):
                self.assertEqual(
                    resolve_link_references(f"[{token}]({destination})", self.agent),
                    f"[{url}]({url})",
                )

    def test_create_csv_resolves_raw_and_query_cells_without_breaking_url_commas(self):
        url = "https://profiles.example.test/avery,chen?view=full#bio"
        token = rewrite_prompt_urls(url, self.agent, create=True)

        for params, query_rows in (
            ({"csv_text": f'name,profile\nAvery,"[Open]({token})"\n', "file_path": "/exports/raw.csv"}, None),
            ({"query": "SELECT name, profile FROM people", "file_path": "/exports/query.csv"}, [{"name": "Avery", "profile": token}]),
        ):
            with self.subTest(file_path=params["file_path"]), patch(
                "api.agent.tools.create_csv.run_sqlite_select",
                return_value=(query_rows, ["name", "profile"], None),
            ), patch(
                "api.agent.tools.create_csv.write_agent_export", return_value={"status": "ok"}
            ) as write_mock:
                self.assertEqual(execute_create_csv(self.agent, params), {"status": "ok"})
                rows = list(csv.reader(io.StringIO(write_mock.call_args.kwargs["content_bytes"].decode())))
                expected = f"[Open]({url})" if query_rows is None else url
                self.assertEqual(rows, [["name", "profile"], ["Avery", expected]])

    def test_create_file_resolves_supported_raw_and_query_documents_and_rejects_code(self):
        url = "https://profiles.example.test/avery?view=full#bio"
        token = rewrite_prompt_urls(url, self.agent, create=True)
        supported = (
            "text/plain", "text/markdown", "text/html", "application/json", "application/ld+json",
            "application/xml", "text/xml", "application/yaml", "text/yaml",
        )

        for mime_type in supported:
            with self.subTest(mime_type=mime_type), patch(
                "api.agent.tools.create_file.write_agent_export", return_value={"status": "ok"}
            ) as write_mock:
                result = execute_create_file(self.agent, {
                    "content": f"Profile: {token}",
                    "file_path": "/exports/report.txt",
                    "mime_type": f"{mime_type}; charset=utf-8",
                })
                self.assertEqual(result, {"status": "ok"})
                self.assertEqual(write_mock.call_args.kwargs["content_bytes"].decode(), f"Profile: {url}")

        with patch(
            "api.agent.tools.create_file.run_sqlite_select",
            return_value=([{"body": f"Profile: {token}"}], ["body"], None),
        ), patch(
            "api.agent.tools.create_file.write_agent_export", return_value={"status": "ok"}
        ) as write_mock:
            result = execute_create_file(self.agent, {
                "query": "SELECT body FROM report",
                "file_path": "/exports/report.md",
                "mime_type": "text/markdown",
            })
            self.assertEqual(result, {"status": "ok"})
            self.assertEqual(write_mock.call_args.kwargs["content_bytes"].decode(), f"Profile: {url}")

        for mime_type in ("text/x-python", "application/javascript", "application/octet-stream"):
            with self.subTest(mime_type=mime_type), patch(
                "api.agent.tools.create_file.write_agent_export"
            ) as write_mock:
                result = execute_create_file(self.agent, {
                    "content": f"value = '{token}'",
                    "file_path": "/exports/source.txt",
                    "mime_type": mime_type,
                })
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["retryable"])
                self.assertIn("create_file.content", result["message"])
                write_mock.assert_not_called()

    @patch("api.agent.tools.create_pdf.get_max_file_size", return_value=None)
    def test_create_pdf_resolves_clickable_and_plain_references_but_blocks_assets(self, _get_max_size):
        url = "https://profiles.example.test/avery?view=full#bio"
        token = rewrite_prompt_urls(url, self.agent, create=True)
        html = f"<a href='{token}'>Avery</a><p>{token}</p>"

        with patch("weasyprint.HTML") as html_mock, patch(
            "api.agent.tools.create_pdf.write_agent_export", return_value={"status": "ok"}
        ):
            html_mock.return_value.write_pdf.return_value = b"%PDF"
            self.assertEqual(
                execute_create_pdf(self.agent, {"html": html, "file_path": "/exports/report.pdf"}),
                {"status": "ok"},
            )
            rendered = html_mock.call_args.kwargs["string"]
            self.assertIn(f"<a href='{url}'>Avery</a>", rendered)
            self.assertIn(f"<p>{url}</p>", rendered)

        with patch("weasyprint.HTML") as html_mock:
            result = execute_create_pdf(self.agent, {
                "html": f"<img src='{token}'>",
                "file_path": "/exports/report.pdf",
            })
            self.assertEqual(result["status"], "error")
            self.assertIn("external or local asset", result["message"])
            html_mock.assert_not_called()

    def test_artifact_tools_return_retryable_missing_malformed_and_foreign_reference_errors(self):
        foreign = rewrite_prompt_urls(
            "https://other.example.test/item",
            self.other_agent,
            create=True,
        )
        cases = (
            (execute_create_csv, {"csv_text": "name,url\nMissing,$[link:L0000000000000000]", "file_path": "/exports/a.csv"}),
            (execute_create_file, {"content": f"Foreign: {foreign}", "mime_type": "text/plain", "file_path": "/exports/a.txt"}),
            (execute_create_pdf, {"html": "<a href='$[link:broken]'>Broken</a>", "file_path": "/exports/a.pdf"}),
        )

        for executor, params in cases:
            with self.subTest(tool=executor.__name__):
                result = executor(self.agent, params)
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["retryable"])
                self.assertTrue("unavailable" in result["message"] or "malformed" in result["message"])

    def test_missing_malformed_and_foreign_references_fail_retryably(self):
        foreign = rewrite_prompt_urls(
            "https://other.example.test/1",
            self.other_agent,
            create=True,
        )
        missing = "$[link:L0000000000000000]"

        for value in (foreign, missing, "$[link:not-a-uuid]", "$[link:missing"):
            with self.subTest(value=value), self.assertRaises(LinkReferenceResolutionError):
                resolve_link_references(value, self.agent)

        valid = rewrite_prompt_urls(
            "https://profiles.example.test/valid",
            self.agent,
            create=True,
        )
        with self.assertRaises(LinkReferenceResolutionError) as raised:
            resolve_link_references(f"{valid} {missing}", self.agent)
        self.assertIn("L0000000000000000", str(raised.exception))
        self.assertIn("other references remain usable", str(raised.exception))

    def test_naked_reference_ids_in_destinations_fail_with_exact_retry_syntax(self):
        token = rewrite_prompt_urls(
            "https://files.example.test/board-pack.pdf",
            self.agent,
            create=True,
        )
        public_id = token.removeprefix("$[link:").removesuffix("]")

        for body in (f"[Download]({public_id})", f"<a href='{public_id}'>Download</a>"):
            with self.subTest(body=body), self.assertRaises(LinkReferenceResolutionError) as raised:
                resolve_link_references(body, self.agent)
            self.assertIn(token, str(raised.exception))

        plain_text = f"Reference ID: {public_id}"
        self.assertEqual(resolve_link_references(plain_text, self.agent), plain_text)

    def test_all_human_message_senders_return_retryable_reference_errors(self):
        malformed = "$[link:not-a-uuid]"
        with patch("api.agent.tools.email_sender.can_bypass_email_verification_for_signup_preview_first_email", return_value=True):
            email_result = execute_send_email(
                self.agent,
                {
                    "to_address": "recipient@example.com",
                    "subject": "Links",
                    "mobile_first_html": f"<a href='{malformed}'>Open</a>",
                    "will_continue_work": False,
                },
            )
        with patch("api.agent.tools.sms_sender.require_verified_email"):
            sms_result = execute_send_sms(
                self.agent,
                {
                    "to_number": "+15555550123",
                    "body": f"[Open]({malformed})",
                    "will_continue_work": False,
                },
            )
        results = [
            execute_send_chat_message(
                self.agent,
                {"body": malformed, "will_continue_work": False},
            ),
            email_result,
            sms_result,
            execute_send_agent_message(
                self.agent,
                {
                    "peer_agent_id": str(self.other_agent.id),
                    "message": malformed,
                    "will_continue_work": False,
                },
            ),
            execute_send_discord_message(
                self.agent,
                {
                    "channel_id": "123456789",
                    "message": malformed,
                    "will_continue_work": False,
                },
            ),
        ]

        for result in results:
            self.assertEqual(result["status"], "error")
            self.assertTrue(result["retryable"])
            self.assertIn("malformed", result["message"])

    def test_filespace_variables_remain_unchanged(self):
        set_agent_variable("/charts/sales.svg", "https://files.example.test/sales.svg")
        self.assertEqual(
            substitute_variables_with_filespace("![]($[/charts/sales.svg])", self.agent),
            "![](https://files.example.test/sales.svg)",
        )

    def test_registration_failure_leaves_source_url_visible(self):
        url = "https://source.example.test/items/7"
        with patch(
            "api.agent.core.link_references.PersistentAgentLinkReference.objects.filter",
            side_effect=DatabaseError("unavailable"),
        ):
            rendered = pair_prompt_urls(
                url,
                self.agent,
                create=True,
            )

        self.assertEqual(rendered, url)

    def test_source_tool_classification_is_explicit(self):
        self.assertTrue(is_source_bearing_tool("http_request"))
        self.assertTrue(is_source_bearing_tool("mcp_vendor_search"))
        self.assertTrue(is_source_bearing_tool("spawn_web_task_result"))
        self.assertFalse(is_source_bearing_tool("spawn_web_task"))
        self.assertFalse(is_source_bearing_tool("sqlite_batch"))
        self.assertFalse(is_source_bearing_tool("python_exec"))

    def test_source_result_preview_pairs_raw_url_with_reference_without_mutating_result(self):
        url = "https://profiles.example.test/avery?view=full#bio"
        raw_result = f'{{"results":[{{"name":"Avery Chen","profile_url":"{url}"}}]}}'
        record = ToolCallResultRecord(
            step_id="00000000-0000-4000-8000-000000000010",
            tool_name="mcp_people_search",
            created_at=datetime.now(timezone.utc),
            result_text=raw_result,
        )

        prompt_info = prepare_tool_results_for_prompt(
            [record],
            recency_positions={record.step_id: 0},
            fresh_tool_call_step_ids={record.step_id},
            url_rewriter=lambda text, item: rewrite_prompt_urls(
                text,
                self.agent,
                create=is_source_bearing_tool(item.tool_name),
            ),
            paired_url_rewriter=lambda text, item: pair_prompt_urls(
                text,
                self.agent,
                create=is_source_bearing_tool(item.tool_name),
            ),
            paired_url_step_ids={record.step_id},
        )[record.step_id]

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent)
        token = f"$[link:{reference.public_id}]"
        self.assertIn("Avery Chen", prompt_info.meta)
        self.assertIn(token, prompt_info.meta)
        self.assertIn(f"{url} [link_ref: {token}]", prompt_info.meta)
        self.assertIn(f"{url} [link_ref: {token}]", prompt_info.preview_text)
        self.assertIn(token, prompt_info.preview_text)
        self.assertEqual(record.result_text, raw_result)

        older_prompt_info = prepare_tool_results_for_prompt(
            [record],
            recency_positions={},
            url_rewriter=lambda text, item: rewrite_prompt_urls(
                text,
                self.agent,
                create=is_source_bearing_tool(item.tool_name),
            ),
            paired_url_rewriter=lambda text, item: pair_prompt_urls(
                text,
                self.agent,
                create=is_source_bearing_tool(item.tool_name),
            ),
            paired_url_step_ids=set(),
        )[record.step_id]
        self.assertIn(token, older_prompt_info.preview_text)
        self.assertNotIn(url, older_prompt_info.preview_text)

    def test_source_result_marks_sparse_item_link_field_as_not_provided(self):
        raw_result = (
            "name=Linked | console_url=https://console.example.test/linked\n"
            "name=Unlinked | console_host=console.example.test | console_route=/unlinked | "
            "profile_id=p_7f2c | directory_slug=unlinked"
        )
        record = ToolCallResultRecord(
            step_id="00000000-0000-4000-8000-000000000012",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=raw_result,
        )

        prompt_info = prepare_tool_results_for_prompt(
            [record],
            recency_positions={record.step_id: 0},
            fresh_tool_call_step_ids={record.step_id},
            url_rewriter=lambda text, item: rewrite_prompt_urls(
                text,
                self.agent,
                create=is_source_bearing_tool(item.tool_name),
            ),
            paired_url_rewriter=lambda text, item: pair_prompt_urls(
                text,
                self.agent,
                create=is_source_bearing_tool(item.tool_name),
            ),
            paired_url_step_ids={record.step_id},
        )[record.step_id]

        self.assertIn(
            "name=Unlinked | console_host=[omitted: no item link] | console_route=[omitted: no item link] | "
            "profile_id=p_7f2c | directory_slug=unlinked | console_url= [not provided]",
            prompt_info.preview_text,
        )
        self.assertNotIn("console_host=console.example.test", prompt_info.meta)
        self.assertIn("profile_id=p_7f2c", prompt_info.preview_text)
        self.assertIn("directory_slug=unlinked", prompt_info.preview_text)
        self.assertEqual(record.result_text, raw_result)

    def test_large_source_focus_replaces_misleading_prefix_and_query_hint(self):
        url = "https://profiles.example.test/alice"
        content = (
            ("Archive note, routine approvals, no source.\n" * 6_000)
            + f"name: Alice\nrole: Controller\nprofile_url: {url}\n"
            + ("Archive note, routine approvals, no source.\n" * 6_000)
        )
        record = ToolCallResultRecord(
            step_id="00000000-0000-4000-8000-000000000014",
            tool_name="http_request",
            created_at=datetime.now(timezone.utc),
            result_text=json.dumps({"status": "ok", "content": content}),
        )

        prompt_info = prepare_tool_results_for_prompt(
            [record],
            recency_positions={record.step_id: 0},
            fresh_tool_call_step_ids={record.step_id},
            url_rewriter=lambda text, item: rewrite_prompt_urls(
                text,
                self.agent,
                create=is_source_bearing_tool(item.tool_name),
            ),
            paired_url_rewriter=lambda text, item: pair_prompt_urls(
                text,
                self.agent,
                create=is_source_bearing_tool(item.tool_name),
            ),
            paired_url_step_ids={record.step_id},
        )[record.step_id]

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent, url=url)
        token = f"$[link:{reference.public_id}]"
        self.assertIn("FOCUS:", prompt_info.meta)
        self.assertIn("name: Alice", prompt_info.meta)
        self.assertIn(f"{url} [link_ref: {token}]", prompt_info.meta)
        self.assertNotIn("CSV DATA", prompt_info.meta)
        self.assertNotIn("Archive note, routine", prompt_info.preview_text)
        self.assertNotIn("LINK OUTPUT", prompt_info.preview_text)

        later_prompt_info = prepare_tool_results_for_prompt(
            [record],
            recency_positions={record.step_id: 0},
            url_rewriter=lambda text, item: rewrite_prompt_urls(text, self.agent, create=False),
        )[record.step_id]
        self.assertIn("FOCUS:", later_prompt_info.meta)
        self.assertIn("name: Alice", later_prompt_info.meta)
        self.assertIn(token, later_prompt_info.meta)
        self.assertNotIn(url, later_prompt_info.meta)

    def test_lookup_result_masks_url_parts_inside_escaped_json(self):
        raw_result = json.dumps({
            "result_text": (
                "name=Linked | console_url=https://console.example.test/linked\\n"
                "name=Unlinked | console_host=console.example.test | console_route=/unlinked"
            )
        })
        reference = PersistentAgentLinkReference.objects.create(
            agent=self.agent,
            url="https://console.example.test/linked",
        )
        record = ToolCallResultRecord(
            step_id="00000000-0000-4000-8000-000000000013",
            tool_name="sqlite_batch",
            created_at=datetime.now(timezone.utc),
            result_text=raw_result,
        )

        prompt_info = prepare_tool_results_for_prompt(
            [record],
            recency_positions={record.step_id: 0},
            fresh_tool_call_step_ids={record.step_id},
            url_rewriter=lambda text, item: rewrite_prompt_urls(text, self.agent, create=False),
        )[record.step_id]

        self.assertIn(f"$[link:{reference.public_id}]", prompt_info.preview_text)
        self.assertNotIn("console.example.test |", prompt_info.preview_text)
        self.assertNotIn("console_route=/unlinked", prompt_info.preview_text)

    def test_full_source_result_registers_deep_urls_before_preview_truncation(self):
        deep_url = "https://profiles.example.test/deep-record?view=full#bio"
        prepared = _PreparedToolExecution(
            idx=0,
            tool_name="http_request",
            tool_params={"url": "https://api.example.test/records", "will_continue_work": True},
            exec_params={"url": "https://api.example.test/records", "will_continue_work": True},
            pending_step=None,
            credits_consumed=None,
            consumed_credit=None,
            call_id="call-source",
            explicit_continue=True,
            inferred_continue=False,
            parallel_safe=False,
            parallel_ineligible_reason=None,
        )
        _finalize_tool_batch(
            self.agent,
            [
                _ToolExecutionOutcome(
                    prepared=prepared,
                    result={"status": "ok", "content": f"{'x' * 50_000}\nprofile_url={deep_url}"},
                    duration_ms=1,
                    updated_tools=None,
                    variable_map={},
                )
            ],
            attach_completion=lambda _kwargs: None,
            attach_prompt_archive=lambda _step: None,
        )

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent, url=deep_url)
        token = f"$[link:{reference.public_id}]"
        stored = PersistentAgentToolCall.objects.get(step__agent=self.agent)
        self.assertIn(deep_url, stored.result)
        self.assertNotIn(token, stored.result)

        derived_record = ToolCallResultRecord(
            step_id="00000000-0000-4000-8000-000000000011",
            tool_name="sqlite_batch",
            created_at=datetime.now(timezone.utc),
            result_text=f'{{"profile_url":"{deep_url}"}}',
        )
        prompt_info = prepare_tool_results_for_prompt(
            [derived_record],
            recency_positions={derived_record.step_id: 0},
            fresh_tool_call_step_ids={derived_record.step_id},
            url_rewriter=lambda text, _item: rewrite_prompt_urls(text, self.agent, create=False),
            paired_url_rewriter=lambda text, _item: pair_prompt_urls(text, self.agent, create=False),
            paired_url_step_ids={derived_record.step_id},
        )[derived_record.step_id]
        self.assertIn(deep_url, prompt_info.preview_text)
        self.assertIn(token, prompt_info.preview_text)

    def test_inbound_prompt_url_becomes_reference_and_raw_message_stays_inspectable(self):
        url = "https://vendors.example.test/acme?plan=pro#pricing"
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address="agent-link-ref",
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address="user-link-ref",
        )
        message = PersistentAgentMessage.objects.create(
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            owner_agent=self.agent,
            is_outbound=False,
            body=f"Compare Acme: {url}",
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            messages, _, _ = build_prompt_context(
                self.agent,
                daily_credit_state={},
                task_credit_available=Decimal("0"),
            )

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent)
        token = f"$[link:{reference.public_id}]"
        system_prompt = next(item["content"] for item in messages if item["role"] == "system")
        user_prompt = next(item["content"] for item in messages if item["role"] == "user")
        self.assertIn(f"Compare Acme: {url} [link_ref: {token}]", user_prompt)
        self.assertEqual(system_prompt.count("## Link References (CRITICAL)"), 1)
        self.assertNotIn("## Link References (CRITICAL)", user_prompt)
        self.assertIn("the raw URL is evidence", system_prompt)
        self.assertIn("adjacent token is only a display/fetch handle", system_prompt)
        self.assertIn("Keep pairs attached", system_prompt)
        self.assertIn("Final Markdown is exactly `[item]($[link:LEXACT])`", system_prompt)
        self.assertIn("replace it with a raw URL", system_prompt)
        self.assertIn("Never encode, edit, reassign, combine, or guess it", system_prompt)
        self.assertIn("Items without a token stay plain", system_prompt)
        self.assertIn("SQL/state/search", system_prompt)
        self.assertIn("A report is unfinished while a token-backed entity name is plain", system_prompt)
        self.assertIn("body must contain the exact tokens", system_prompt)
        message.refresh_from_db()
        self.assertEqual(message.body, f"Compare Acme: {url}")

    def test_compacted_history_uses_registered_reference_without_raw_url(self):
        url = "https://vendors.example.test/acme/history"
        token = rewrite_prompt_urls(url, self.agent, create=True)
        PersistentAgentCommsSnapshot.objects.create(
            agent=self.agent,
            snapshot_until=django_timezone.now(),
            summary=f"Previously reviewed Acme at {url}",
        )

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            messages, _, _ = build_prompt_context(
                self.agent,
                daily_credit_state={},
                task_credit_available=Decimal("0"),
            )

        prompt = "\n".join(item["content"] for item in messages)
        self.assertIn(f"Previously reviewed Acme at {token}", prompt)
        self.assertNotIn(url, prompt)

    def test_prompt_reference_is_stable_across_renders(self):
        url = "https://profiles.example.test/avery?view=full#bio"

        first = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
        )
        second = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
        )

        self.assertEqual(first, second)
        self.assertRegex(first, r"^\$\[link:L[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{16}\]$")

    def test_system_prompt_contains_one_canonical_reference_rule(self):
        prompt = _get_system_instruction(self.agent, is_first_run=False)

        self.assertEqual(prompt.count("## Link References (CRITICAL)"), 1)
        self.assertEqual(prompt.count("$[link:L…]"), 1)
        self.assertNotIn("LINK OUTPUT CONTRACT", prompt)
        self.assertIn("build/create custom tool -> create_custom_tool first", prompt)
        self.assertIn("supplied URLs -> opaque runtime inputs", prompt)
        self.assertNotIn("Message delivery blocked", prompt)

    def test_http_request_url_schema_accepts_link_references(self):
        properties = get_http_request_tool()["function"]["parameters"]["properties"]

        self.assertIn("$[link:id]", properties["url"]["description"])
        self.assertIn("$[link:id]", properties["body"]["description"])

    def test_raw_urls_are_not_blocked_at_delivery_preparation(self):
        raw_url = "https://unseen.example.test/items/42"
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "send_chat_message",
                "arguments": json.dumps({
                    "body": f"[Open]({raw_url})",
                    "will_continue_work": False,
                }),
            },
        }

        with patch(
            "api.agent.core.event_processing.get_agent_daily_credit_state",
            return_value=None,
        ), patch(
            "api.agent.core.event_processing._resolve_tool_for_execution",
            return_value=("send_chat_message", None),
        ):
            prepared = _prepare_tool_batch(
                self.agent,
                tool_calls=[tool_call],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=True,
                attach_completion=lambda _kwargs: None,
                attach_prompt_archive=lambda _step: None,
            )

        self.assertEqual(len(prepared.prepared_calls), 1)
        self.assertFalse(prepared.followup_required)
