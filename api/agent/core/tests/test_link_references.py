import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase, override_settings, tag

from api.agent.core.link_references import (
    LinkReferenceResolutionError,
    extract_http_urls,
    is_source_bearing_tool,
    resolve_link_reference_params,
    resolve_link_references,
    rewrite_prompt_urls,
)
from api.agent.core.event_processing import _execute_tool_call_runtime, _prepare_tool_batch
from api.agent.core.prompt_context import _get_system_instruction, build_prompt_context
from api.agent.core.tool_results import ToolCallResultRecord, prepare_tool_results_for_prompt
from api.agent.tools.agent_variables import (
    clear_variables,
    set_agent_variable,
    substitute_variables_with_filespace,
)
from api.agent.tools.email_sender import execute_send_email
from api.agent.tools.peer_dm import execute_send_agent_message
from api.agent.tools.send_discord_message import execute_send_discord_message
from api.agent.tools.sms_sender import execute_send_sms
from api.agent.tools.sqlite_batch import _annotate_item_links, _row_url_reporting_note
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentLinkReference,
    PersistentAgentMessage,
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

    def test_registration_deduplicates_and_preserves_exact_url_and_first_source(self):
        url = "https://items.example.test/42/path?view=full&region=west#details"
        first = rewrite_prompt_urls(
            f"[Item]({url})",
            self.agent,
            create=True,
            source_kind="inbound_message",
            source_object_id="message-1",
        )
        second = rewrite_prompt_urls(
            f"<a href='{url}'>Item</a>",
            self.agent,
            create=True,
            source_kind="tool_result",
            source_object_id="result-2",
        )

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent)
        token = f"$[link:{reference.public_id}]"
        self.assertEqual(reference.url, url)
        self.assertRegex(reference.public_id, r"^L[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{16}$")
        self.assertEqual(reference.source_kind, "inbound_message")
        self.assertEqual(reference.source_object_id, "message-1")
        self.assertEqual(PersistentAgentLinkReference.objects.count(), 1)
        self.assertEqual(first, f"[Item]({token})")
        self.assertEqual(second, f"<a href='{token}'>Item</a>")

    def test_lookup_only_does_not_create_provenance(self):
        url = "https://derived.example.test/records/9"
        self.assertEqual(
            rewrite_prompt_urls(url, self.agent, create=False),
            url,
        )
        self.assertFalse(PersistentAgentLinkReference.objects.exists())

        registered = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
            source_kind="tool_result",
            source_object_id="source-step",
        )
        self.assertEqual(rewrite_prompt_urls(url, self.agent, create=False), registered)

    def test_reference_resolves_across_calls_in_markdown_html_and_plain_text(self):
        url = "https://profiles.example.test/avery?campaign=q3#experience"
        token = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
            source_kind="tool_result",
            source_object_id="step-1",
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

    @override_settings(PUBLIC_SITE_URL="https://gobii.example.test")
    def test_model_rendered_reference_routes_resolve_for_any_same_origin_path(self):
        url = "https://profiles.example.test/avery?campaign=q3#experience"
        token = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
            source_kind="tool_result",
            source_object_id="step-1",
        )
        reference_id = token.removeprefix("$[link:").removesuffix("]")
        body = (
            f"[Profile](https://gobii.example.test/link/{reference_id}) "
            f"<a href='https://gobii.example.test/app/file/{reference_id}'>HTML</a> "
            f"Download: https://gobii.example.test/dl/{reference_id} "
            f"API: https://gobii.example.test/api/links/{reference_id}"
        )

        self.assertEqual(
            resolve_link_references(body, self.agent),
            (
                f"[Profile]({url}) <a href='{url}'>HTML</a> "
                f"Download: {url} "
                f"API: {url}"
            ),
        )

        with self.assertRaises(LinkReferenceResolutionError):
            resolve_link_references(
                "https://gobii.example.test/api/links/L0000000000000000",
                self.agent,
            )

    @override_settings(PUBLIC_SITE_URL="https://gobii.example.test")
    def test_tool_params_resolve_only_complete_reference_values_recursively(self):
        url = "https://profiles.example.test/avery?campaign=q3#experience"
        token = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
            source_kind="tool_result",
            source_object_id="step-1",
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
                "related": [url],
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
            source_kind="tool_result",
            source_object_id="step-1",
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

    def test_missing_malformed_and_foreign_references_fail_retryably(self):
        foreign = rewrite_prompt_urls(
            "https://other.example.test/1",
            self.other_agent,
            create=True,
            source_kind="inbound_message",
            source_object_id="other-message",
        )
        missing = "$[link:L0000000000000000]"

        for value in (foreign, missing, "$[link:not-a-uuid]", "$[link:missing"):
            with self.subTest(value=value), self.assertRaises(LinkReferenceResolutionError):
                resolve_link_references(value, self.agent)

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
            rendered = rewrite_prompt_urls(
                url,
                self.agent,
                create=True,
                source_kind="inbound_message",
                source_object_id="message-7",
            )

        self.assertEqual(rendered, url)

    def test_source_tool_classification_is_explicit(self):
        self.assertTrue(is_source_bearing_tool("http_request"))
        self.assertTrue(is_source_bearing_tool("mcp_vendor_search"))
        self.assertTrue(is_source_bearing_tool("spawn_web_task_result"))
        self.assertFalse(is_source_bearing_tool("spawn_web_task"))
        self.assertFalse(is_source_bearing_tool("sqlite_batch"))
        self.assertFalse(is_source_bearing_tool("python_exec"))

    def test_source_result_preview_uses_reference_without_mutating_raw_result(self):
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
                source_kind="tool_result",
                source_object_id=item.step_id,
            ),
        )[record.step_id]

        reference = PersistentAgentLinkReference.objects.get(agent=self.agent)
        token = f"$[link:{reference.public_id}]"
        self.assertIn("Avery Chen", prompt_info.meta)
        self.assertIn(token, prompt_info.meta)
        self.assertNotIn(url, prompt_info.meta)
        self.assertIn(token, prompt_info.preview_text)
        self.assertEqual(record.result_text, raw_result)

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
        user_prompt = next(item["content"] for item in messages if item["role"] == "user")
        self.assertIn(f"Compare Acme: $[link:{reference.public_id}]", user_prompt)
        message.refresh_from_db()
        self.assertEqual(message.body, f"Compare Acme: {url}")

    def test_prompt_reference_is_stable_across_renders(self):
        url = "https://profiles.example.test/avery?view=full#bio"

        first = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
            source_kind="inbound_message",
            source_object_id="message-1",
        )
        second = rewrite_prompt_urls(
            url,
            self.agent,
            create=True,
            source_kind="tool_result",
            source_object_id="result-2",
        )

        self.assertEqual(first, second)
        self.assertRegex(first, r"^\$\[link:L[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{16}\]$")

    def test_system_prompt_has_one_reference_rule(self):
        prompt = _get_system_instruction(self.agent, is_first_run=False)

        self.assertEqual(prompt.count("Provided link-reference tokens are destinations"), 1)
        self.assertIn("No token means no entity link", prompt)
        self.assertNotIn("Message delivery blocked", prompt)

    def test_sqlite_rows_make_item_link_availability_explicit(self):
        url = "https://console.example.test/services/svc_1?region=east#status"
        rows = [
            {"name": "Linked", "console_url": url, "service_id": "svc_1"},
            {
                "name": "Unlinked",
                "console_url": None,
                "console_host": "console.example.test",
                "console_route": "/services/svc_2",
            },
        ]

        _annotate_item_links(rows)

        self.assertEqual(rows[0]["item_link"], url)
        self.assertEqual(rows[1]["item_link"], "none")
        note = _row_url_reporting_note(rows)
        self.assertIn("item_link=none stays unlinked", note)
        self.assertIn("Host, route, slug, and ID fields are not links", note)

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
