"""Tests for event processing continuation decisions."""

import json
import os
import sqlite3
import tempfile

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag
from django.utils import timezone

from api.agent.core.event_processing import (
    _finalize_tool_batch,
    _contact_permission_params_from_misrouted_human_input,
    _ensure_credit_for_tool,
    _process_agent_events_locked,
    _infer_retryable_from_text,
    _is_warning_status,
    _normalize_error_result,
    _normalize_tool_params,
    _parse_tool_call_params,
    _PreparedToolExecution,
    _sanitize_tool_name,
    _should_infer_message_tool_continuation,
    _should_imply_continue,
    _should_skip_stale_planning_mode_after_terminal_delivery,
    _ToolExecutionOutcome,
    _tool_call_likely_terminal_message,
)
from api.agent.core.burn_control import BurnRateAction, handle_burn_rate_limit
from api.agent.core.llm_config import AgentLLMTier, clear_runtime_tier_override, get_runtime_tier_override
from api.agent.core.period_events import DAILY_SOFT_LIMIT_EXCEEDED_EVENT, should_emit_daily_agent_event
from django.urls import reverse

from api.agent.core.prompt_context import build_prompt_context, build_prompt_context_preview
from api.agent.peer_comm import PeerMessagingError
from api.agent.tools.sqlite_state import reset_sqlite_db_path, set_sqlite_db_path
from api.agent.tools.peer_dm import execute_send_agent_message
from api.agent.tools.tool_manager import _normalize_tool_params_unicode_escapes
from api.agent.tools.web_chat_sender import _looks_like_routine_progress_message
from api.models import (
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    SmsContactPurpose,
)
from util.urls import build_agent_detail_url, build_site_url


class _DummySpan:
    def add_event(self, *_args, **_kwargs):
        return None

    def set_attribute(self, *_args, **_kwargs):
        return None


@tag('batch_event_processing')
class ImpliedContinuationDecisionTests(SimpleTestCase):
    def test_implies_continue_with_canonical_phrase(self):
        result = _should_imply_continue(
            has_canonical_continuation=True,
            has_other_tool_calls=False,
            has_explicit_sleep=False,
        )

        self.assertTrue(result)

    def test_implies_continue_with_other_tool_calls(self):
        result = _should_imply_continue(
            has_canonical_continuation=False,
            has_other_tool_calls=True,
            has_explicit_sleep=False,
        )

        self.assertTrue(result)

    def test_does_not_continue_without_signal_or_tools(self):
        result = _should_imply_continue(
            has_canonical_continuation=False,
            has_other_tool_calls=False,
            has_explicit_sleep=False,
        )

        self.assertFalse(result)

    def test_explicit_sleep_overrides_continuation(self):
        result = _should_imply_continue(
            has_canonical_continuation=True,
            has_other_tool_calls=True,
            has_explicit_sleep=True,
        )

        self.assertFalse(result)

    def test_natural_continuation_without_canonical_signal_stops(self):
        result = _should_imply_continue(
            has_canonical_continuation=False,
            has_other_tool_calls=False,
            has_explicit_sleep=False,
        )

        self.assertFalse(result)

    def test_no_signal_stops(self):
        result = _should_imply_continue(
            has_canonical_continuation=False,
            has_other_tool_calls=False,
            has_explicit_sleep=False,
        )

        self.assertFalse(result)

    def test_standby_acknowledgement_does_not_infer_continuation(self):
        result = _should_infer_message_tool_continuation(
            "Got it. No follow-ups unless you say so; I'll be right here whenever you need me."
        )

        self.assertFalse(result)

    def test_when_you_need_me_acknowledgement_does_not_infer_continuation(self):
        result = _should_infer_message_tool_continuation(
            "Got it! I'll be right here when you need me."
        )

        self.assertFalse(result)


@tag('batch_event_processing')
class ToolErrorNormalizationTests(SimpleTestCase):
    def test_native_http_error_preserves_response_context(self):
        result = _normalize_error_result(
            {
                "status": "error",
                "message": "Google Drive API request failed with status 400.",
                "status_code": 400,
                "retryable": False,
                "provider_key": "google_drive",
                "provider_name": "Google Drive",
                "method": "POST",
                "url": "https://sheets.googleapis.com/v4/spreadsheets/sheet-123:batchUpdate",
                "guidance": "Check the Google Sheets or Drive API request shape.",
                "api_error_message": "Invalid JSON payload received.",
                "headers": {"Content-Type": "application/json"},
                "content": {
                    "error": {
                        "code": 400,
                        "message": "Invalid JSON payload received.",
                    }
                },
            }
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["status_code"], 400)
        self.assertEqual(result["provider_key"], "google_drive")
        self.assertEqual(result["method"], "POST")
        self.assertIn("sheets.googleapis.com", result["url"])
        self.assertEqual(result["api_error_message"], "Invalid JSON payload received.")
        self.assertEqual(result["content"]["error"]["message"], "Invalid JSON payload received.")
        self.assertEqual(result["headers"]["Content-Type"], "application/json")

    def test_non_native_error_uses_minimal_payload(self):
        result = _normalize_error_result(
            {
                "status": "error",
                "message": "Tool failed.",
                "content": {"error": {"message": "Should not be persisted for generic errors."}},
            }
        )

        self.assertEqual(result, {"status": "error", "message": "Tool failed.", "retryable": False})


@tag('batch_event_processing')
class MessageToolExplicitContinuationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="message-continuation@example.com",
            email="message-continuation@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="MessageContinuationBA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Message Continuation Agent",
            charter="Handle user work.",
            browser_use_agent=browser_agent,
        )

    def test_explicit_continue_true_is_not_overridden_by_message_heuristic(self):
        body = (
            "Sorry Yash! I was getting the searches set up — starting the actual profile discovery "
            "right now. Will have the Excel sheet to you within a couple of hours!"
        )
        self.assertFalse(_should_infer_message_tool_continuation(body))
        prepared = _PreparedToolExecution(
            idx=1,
            tool_name="send_chat_message",
            tool_params={"body": body, "will_continue_work": True},
            exec_params={"body": body, "will_continue_work": True},
            pending_step=None,
            credits_consumed=None,
            consumed_credit=None,
            call_id="call_1",
            explicit_continue=True,
            inferred_continue=False,
            parallel_safe=False,
            parallel_ineligible_reason=None,
        )

        finalized = _finalize_tool_batch(
            self.agent,
            [
                _ToolExecutionOutcome(
                    prepared=prepared,
                    result={
                        "status": "ok",
                        "message": "Web chat message sent.",
                        "message_id": str(uuid4()),
                        "auto_sleep_ok": False,
                    },
                    duration_ms=1,
                    updated_tools=None,
                    variable_map={},
                )
            ],
            attach_completion=lambda kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertFalse(finalized.followup_required)
        self.assertTrue(finalized.message_delivery_ok)
        self.assertTrue(finalized.progress_message_delivery_ok)
        self.assertFalse(finalized.terminal_message_delivery_ok)
        self.assertIs(finalized.last_explicit_continue, True)

    def test_preflight_terminal_message_detection_respects_explicit_continue_true(self):
        call = {
            "function": {
                "name": "send_chat_message",
                "arguments": json.dumps(
                    {
                        "body": (
                            "Sorry Yash! I was getting the searches set up — starting the actual "
                            "profile discovery right now."
                        ),
                        "will_continue_work": True,
                    }
                ),
            }
        }

        self.assertFalse(_tool_call_likely_terminal_message(call))

    def test_terminal_message_with_unfinished_plan_requires_followup(self):
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Synthesize final report",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )
        prepared = _PreparedToolExecution(
            idx=1,
            tool_name="send_chat_message",
            tool_params={"body": "Here is the final report.", "will_continue_work": False},
            exec_params={"body": "Here is the final report.", "will_continue_work": False},
            pending_step=None,
            credits_consumed=None,
            consumed_credit=None,
            call_id="call_1",
            explicit_continue=False,
            inferred_continue=False,
            parallel_safe=False,
            parallel_ineligible_reason=None,
        )

        finalized = _finalize_tool_batch(
            self.agent,
            [
                _ToolExecutionOutcome(
                    prepared=prepared,
                    result={
                        "status": "ok",
                        "message": "Web chat message sent.",
                        "message_id": str(uuid4()),
                        "auto_sleep_ok": True,
                    },
                    duration_ms=1,
                    updated_tools=None,
                    variable_map={},
                )
            ],
            attach_completion=lambda kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertTrue(finalized.terminal_message_delivery_ok)
        self.assertTrue(finalized.followup_required)
        self.assertIs(finalized.last_explicit_continue, False)
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__contains="current plan still has unfinished items",
            ).exists()
        )

    def test_daily_limit_terminal_message_with_unfinished_plan_can_stop(self):
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Synthesize final report",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )
        prepared = _PreparedToolExecution(
            idx=1,
            tool_name="send_chat_message",
            tool_params={"body": "I hit today's limit. Please raise it to continue.", "will_continue_work": False},
            exec_params={"body": "I hit today's limit. Please raise it to continue.", "will_continue_work": False},
            pending_step=None,
            credits_consumed=None,
            consumed_credit=None,
            call_id="call_1",
            explicit_continue=False,
            inferred_continue=False,
            parallel_safe=False,
            parallel_ineligible_reason=None,
        )

        finalized = _finalize_tool_batch(
            self.agent,
            [
                _ToolExecutionOutcome(
                    prepared=prepared,
                    result={
                        "status": "ok",
                        "message": "Web chat message sent.",
                        "message_id": str(uuid4()),
                        "auto_sleep_ok": True,
                    },
                    duration_ms=1,
                    updated_tools=None,
                    variable_map={},
                )
            ],
            daily_credit_state={
                "hard_limit": Decimal("5"),
                "hard_limit_remaining": Decimal("0"),
                "used": Decimal("5"),
            },
            attach_completion=lambda kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertTrue(finalized.terminal_message_delivery_ok)
        self.assertFalse(finalized.followup_required)
        self.assertIs(finalized.last_explicit_continue, False)
        self.assertFalse(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__contains="current plan still has unfinished items",
            ).exists()
        )

    def test_planning_mode_terminal_message_with_unfinished_plan_allows_stale_skip(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state"])
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Synthesize final report",
            status=PersistentAgentKanbanCard.Status.TODO,
            priority=1,
        )
        prepared = _PreparedToolExecution(
            idx=1,
            tool_name="send_chat_message",
            tool_params={"body": "Here is the answer.", "will_continue_work": False},
            exec_params={"body": "Here is the answer.", "will_continue_work": False},
            pending_step=None,
            credits_consumed=None,
            consumed_credit=None,
            call_id="call_1",
            explicit_continue=False,
            inferred_continue=False,
            parallel_safe=False,
            parallel_ineligible_reason=None,
        )

        finalized = _finalize_tool_batch(
            self.agent,
            [
                _ToolExecutionOutcome(
                    prepared=prepared,
                    result={
                        "status": "ok",
                        "message": "Web chat message sent.",
                        "message_id": str(uuid4()),
                        "auto_sleep_ok": True,
                    },
                    duration_ms=1,
                    updated_tools=None,
                    variable_map={},
                )
            ],
            attach_completion=lambda kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertTrue(finalized.terminal_message_delivery_ok)
        self.assertFalse(finalized.followup_required)
        self.assertTrue(
            _should_skip_stale_planning_mode_after_terminal_delivery(
                self.agent,
                finalized,
                followup_required=finalized.followup_required,
            )
        )
        self.assertFalse(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__contains="current plan still has unfinished items",
            ).exists()
        )


@tag('batch_event_processing')
class ToolParamUnicodeNormalizationTests(SimpleTestCase):
    def test_decodes_nested_surrogate_pair_escapes(self):
        params = {
            "subject": r"Re: \ud83d\udea8 Breaking",
            "payload": {
                "embeds": [
                    {
                        "title": r"\ud83d\ude80 Launch",
                        "description": r"Fixes shipped \ud83d\udca8",
                    }
                ]
            },
            "count": 3,
        }

        normalized = _normalize_tool_params_unicode_escapes(params)

        self.assertEqual(normalized["subject"], "Re: 🚨 Breaking")
        self.assertEqual(normalized["payload"]["embeds"][0]["title"], "🚀 Launch")
        self.assertEqual(normalized["payload"]["embeds"][0]["description"], "Fixes shipped 💨")
        self.assertEqual(normalized["count"], 3)

    def test_leaves_non_string_values_unchanged(self):
        params = {"active": True, "attempts": 2, "meta": None, "items": [1, False, None]}

        normalized = _normalize_tool_params_unicode_escapes(params)

        self.assertEqual(normalized, params)


@tag('batch_event_processing')
class ToolParamParsingTests(SimpleTestCase):
    def test_empty_string_arguments_are_treated_as_empty_object(self):
        raw_text, tool_params = _parse_tool_call_params("")

        self.assertEqual(raw_text, "")
        self.assertEqual(tool_params, {})

    def test_preserves_escaped_newlines_inside_nested_json_string_payload(self):
        raw_args = (
            '{"content":"{\\n'
            '  \\"instructions\\": \\"line1\\\\nline2\\",\\n'
            '  \\"source_code\\": \\"import os\\\\nprint(1)\\"\\n'
            '}","file_path":"/exports/agent_export.json","mime_type":"application/json"}'
        )

        raw_text, tool_params = _parse_tool_call_params(raw_args)

        self.assertEqual(raw_text, raw_args)
        self.assertIn("\\n", tool_params["content"])
        self.assertNotIn('line1\nline2', tool_params["content"])
        self.assertIn('line1\\nline2', tool_params["content"])
        self.assertIn('import os\\nprint(1)', tool_params["content"])

    def test_http_url_normalization_strips_leaked_dsml_markup(self):
        params = {
            "method": "GET",
            "url": (
                "https://api.coindesk.com/v1/bpi/currentprice/USD.json\n"
                '<｜DSML｜parameter name="will_continue_work" string="false">true'
            ),
            "will_continue_work": True,
        }

        normalized = _normalize_tool_params("http_request", params)

        self.assertEqual(normalized["url"], "https://api.coindesk.com/v1/bpi/currentprice/USD.json")
        self.assertTrue(normalized["will_continue_work"])

    def test_http_url_normalization_preserves_google_drive_query_quotes(self):
        url = (
            "https://www.googleapis.com/drive/v3/files?"
            "q=mimeType%20%3D%20'application%2Fvnd.google-apps.spreadsheet'"
            "%20and%20trashed%20%3D%20false"
        )

        normalized = _normalize_tool_params("http_request", {"method": "GET", "url": url, "will_continue_work": True})

        self.assertEqual(normalized["url"], url)

    def test_brightdata_markdown_scrape_drops_extraction_prompt_when_url_is_known(self):
        normalized = _normalize_tool_params(
            "mcp_brightdata_scrape_as_markdown",
            {
                "url": "https://example.test/blog",
                "prompt": "Extract all details about Example in warehouse automation.",
            },
        )

        self.assertEqual(normalized, {"url": "https://example.test/blog"})

    def test_tool_name_normalization_strips_repeated_mcp_prefix(self):
        self.assertEqual(
            _sanitize_tool_name("mcp_brightdata_scrape_as_mcp_brightdata_scrape_as_markdown"),
            "mcp_brightdata_scrape_as_markdown",
        )

    def test_misrouted_sms_approval_question_becomes_contact_permission(self):
        agent = SimpleNamespace(
            planning_state=PersistentAgent.PlanningState.COMPLETED,
            is_recipient_whitelisted=lambda _channel, _address: False,
        )
        result = _contact_permission_params_from_misrouted_human_input(
            agent,
            {"question": "Do you want me to text +15555550123 that the build finished successfully?"},
        )
        contact = result["contacts"][0]
        self.assertEqual(contact["channel"], "sms")
        self.assertEqual(contact["address"], "+15555550123")
        self.assertEqual(contact["purpose"], "Send requested message")
        self.assertEqual(contact["sms_contact_purpose"], SmsContactPurpose.OTHER_OPERATIONAL)


@tag("batch_event_processing")
class WebChatProgressSuppressionTests(SimpleTestCase):
    def test_suppresses_current_research_progress_with_let_me_grab(self):
        self.assertTrue(
            _looks_like_routine_progress_message(
                "I have search results pointing to the YC Winter 2026 batch. "
                "Let me grab the substantive details from the blog post and the companies page"
            )
        )

    def test_suppresses_progress_after_context_sentence(self):
        self.assertTrue(
            _looks_like_routine_progress_message(
                "The skill is already enabled. Let me fetch Frederick, MD's coordinates and then the current weather."
            )
        )

    def test_suppresses_investigation_progress(self):
        self.assertTrue(
            _looks_like_routine_progress_message(
                "Let me investigate what's happening with the API response more carefully."
            )
        )

    def test_suppresses_proper_search_progress_after_context_sentence(self):
        self.assertTrue(
            _looks_like_routine_progress_message(
                "Those earlier search results came back with placeholder data. "
                "Let me do proper searches on real job platforms"
            )
        )

    def test_suppresses_find_better_way_progress(self):
        self.assertTrue(
            _looks_like_routine_progress_message(
                "The search tool keeps returning fabricated links, so let me find a better way to get live listings."
            )
        )

    def test_suppresses_adverbial_search_progress(self):
        self.assertTrue(
            _looks_like_routine_progress_message(
                "The search result I got back looks fabricated - generic company names and fake job IDs. "
                "Let me actually search real job boards for current listings."
            )
        )

    def test_suppresses_acknowledged_search_result_status(self):
        progress_only = (
            "Good, I have the search results identifying YC Winter 2026 as the latest batch.",
            "Great, I've got the data. Let me update the charter and schedule, then report back.",
            "The last step was incomplete - the query results were fetched but never formatted. Let me fix that now",
            "All four sources are fetched. Now I'll run the clean aggregate query.",
            "All 4 JSON endpoints are fetched. Now I'll query the working table.",
            "The data is in. Let me run the final analysis query and deliver the recommendation",
            "The `plan_candidates` table is populated with 8 rows. Now let me query it for the best plan.",
            "Hey! I'm Eval Agent. Let's dig up three current remote job listings from different sources right now.",
        )
        final_answers = (
            "All four pages are scraped and the comparison query is done. Here's the analysis:\n\n## Support Automation Platform Comparison\n\n**Source pages:**",
            "I have all four source texts. Let me analyze the claims.\n\n**Claims extracted:**\n| Source | Claim |",
        )
        for message in progress_only:
            self.assertTrue(_looks_like_routine_progress_message(message))
        for message in final_answers:
            self.assertFalse(_looks_like_routine_progress_message(message))

    def test_suppresses_first_check_progress(self):
        self.assertTrue(
            _looks_like_routine_progress_message(
                "I need to read the current file, then update it to make `input_urls` and `output_table` required params."
            )
        )


@tag("batch_event_processing")
class PeerMessageToolHandlingTests(SimpleTestCase):
    def test_mcp_session_death_errors_are_retryable(self):
        self.assertTrue(_infer_retryable_from_text("Connection closed"))
        self.assertTrue(
            _infer_retryable_from_text(
                "Client failed to connect: Server session was closed unexpectedly"
            )
        )

    def test_debounced_and_throttled_results_require_followup(self):
        self.assertTrue(_is_warning_status({"status": "debounced"}))
        self.assertTrue(_is_warning_status({"status": "throttled"}))
        self.assertTrue(_is_warning_status({"status": "warning"}))
        self.assertFalse(_is_warning_status({"status": "ok"}))

    def test_send_agent_message_marks_debounce_retryable(self):
        agent = SimpleNamespace(id=uuid4())
        peer_agent = SimpleNamespace(id=uuid4())
        retry_at = timezone.now()

        with patch(
            "api.agent.tools.peer_dm.PersistentAgent.objects.get",
            return_value=peer_agent,
        ), patch(
            "api.agent.tools.peer_dm.resolve_filespace_attachments",
            return_value=[],
        ), patch("api.agent.tools.peer_dm.PeerMessagingService") as service_cls:
            service_cls.return_value.send_message.side_effect = PeerMessagingError(
                "Peer messaging suppressed to avoid a rapid loop.",
                status="debounced",
                retry_at=retry_at,
            )

            result = execute_send_agent_message(
                agent,
                {
                    "peer_agent_id": str(peer_agent.id),
                    "message": "Status update",
                    "will_continue_work": False,
                },
            )

        self.assertEqual(result["status"], "debounced")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["retry_at_iso"], retry_at.isoformat())


@tag("batch_event_processing")
class DailyLimitPromptContextTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="daily-limit-prompt@example.com",
            email="daily-limit-prompt@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="DailyLimitPromptBA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Daily Limit Prompt Agent",
            charter="Handle the user's work",
            browser_use_agent=browser_agent,
        )

    def _render_prompt_content(self, daily_state):
        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            context, _, _ = build_prompt_context(
                self.agent,
                daily_credit_state=daily_state,
            )

        user_message = next(message for message in context if message["role"] == "user")
        return user_message["content"]

    def test_prompt_collapses_equal_soft_and_hard_limits_with_80_percent_warning(self):
        next_reset = timezone.now()
        content = self._render_prompt_content(
            {
                "hard_limit": Decimal("100"),
                "hard_limit_remaining": Decimal("20"),
                "soft_target": Decimal("100"),
                "soft_target_remaining": Decimal("20"),
                "soft_target_exceeded": False,
                "used": Decimal("80"),
                "next_reset": next_reset,
            }
        )

        self.assertIn("Daily limit progress: 80/100", content)
        self.assertIn("Getting tired (80%+)", content)
        self.assertIn(f"resume. Next reset at {next_reset.isoformat()}", content)
        self.assertNotIn("Soft target progress", content)
        self.assertNotIn("Hard limit progress", content)
        self.assertNotIn("you will not be stopped immediately", content)

    def test_prompt_includes_reset_with_hard_limit_when_soft_target_unset(self):
        next_reset = timezone.now()
        content = self._render_prompt_content(
            {
                "hard_limit": Decimal("100"),
                "hard_limit_remaining": Decimal("70"),
                "soft_target": None,
                "soft_target_remaining": None,
                "soft_target_exceeded": False,
                "used": Decimal("30"),
                "next_reset": next_reset,
            }
        )

        self.assertIn("Hard limit progress: 30/100", content)
        self.assertIn(f"Next reset at {next_reset.isoformat()}", content)
        self.assertNotIn("Soft target progress", content)

    @override_settings(PUBLIC_SITE_URL="https://example.com")
    def test_prompt_includes_daily_limit_message_only_links(self):
        daily_state = {
            "hard_limit": Decimal("2"),
            "hard_limit_remaining": Decimal("0"),
            "soft_target": Decimal("1"),
            "soft_target_remaining": Decimal("0"),
            "soft_target_exceeded": True,
            "used": Decimal("2"),
            "next_reset": timezone.now(),
        }
        settings_url = build_agent_detail_url(self.agent.id, self.agent.organization_id)
        double_limit_url_prefix = build_site_url(
            reverse(
                "agent_daily_limit_action",
                kwargs={"pk": self.agent.id, "action": "double"},
            )
        )
        unlimited_limit_url_prefix = build_site_url(
            reverse(
                "agent_daily_limit_action",
                kwargs={"pk": self.agent.id, "action": "unlimited"},
            )
        )

        content = self._render_prompt_content(daily_state)
        self.assertIn("DAILY HARD LIMIT MODE", content)
        self.assertIn(settings_url, content)
        self.assertIn(f"double {double_limit_url_prefix}?token=", content)
        self.assertIn(f"unlimited {unlimited_limit_url_prefix}?token=", content)
        self.assertIn("Only message and sleep tools are available until the user raises the limit", content)
        self.assertIn("sleep_until_next_trigger", content)
        self.assertIn("Once the user raises the limit, you can continue the task.", content)


@tag("batch_event_processing")
class ContactPromptTruncationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="contact-prompt@example.com",
            email="contact-prompt@example.com",
            password="secret",
        )
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        browser_agent = BrowserUseAgent.objects.create(user=user, name="ContactPromptBA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Contact Prompt Agent",
            charter="Handle outreach",
            browser_use_agent=browser_agent,
        )

    def _render_prompt_content(self, db_path):
        token = set_sqlite_db_path(db_path)
        try:
            with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
                "api.agent.core.prompt_context.ensure_comms_compacted"
            ), patch(
                "api.agent.core.prompt_context.get_llm_config_with_failover",
                return_value=[("endpoint", "openai/gpt-4o-mini", {})],
            ):
                context, _, _ = build_prompt_context(self.agent)
        finally:
            reset_sqlite_db_path(token)

        user_message = next(message for message in context if message["role"] == "user")
        return user_message["content"]

    def test_large_allowlist_prompt_samples_recent_activity_and_keeps_sqlite_full(self):
        old_time = timezone.now() - timedelta(days=30)
        updated_time = timezone.now() - timedelta(hours=2)
        conversation_time = timezone.now() - timedelta(minutes=5)

        old_contacts = [
            CommsAllowlistEntry(
                agent=self.agent,
                channel=CommsChannel.EMAIL,
                address=f"old-{index:02d}@example.com",
                is_active=True,
                allow_inbound=True,
                allow_outbound=True,
            )
            for index in range(30)
        ]
        recent_conversation = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="recent-conversation@example.com",
            is_active=True,
            allow_inbound=True,
            allow_outbound=True,
        )
        newly_updated = CommsAllowlistEntry(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="newly-updated@example.com",
            is_active=True,
            allow_inbound=True,
            allow_outbound=True,
        )
        CommsAllowlistEntry.objects.bulk_create(
            [*old_contacts, recent_conversation, newly_updated]
        )
        CommsAllowlistEntry.objects.filter(agent=self.agent).update(
            created_at=old_time,
            updated_at=old_time,
        )
        CommsAllowlistEntry.objects.filter(address="newly-updated@example.com").update(
            updated_at=updated_time
        )

        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
        )
        contact_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="recent-conversation@example.com",
        )
        message = PersistentAgentMessage.objects.create(
            from_endpoint=agent_endpoint,
            to_endpoint=contact_endpoint,
            is_outbound=True,
            body="Recent outreach",
        )
        PersistentAgentMessage.objects.filter(id=message.id).update(timestamp=conversation_time)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.db")
            content = self._render_prompt_content(db_path)

            self.assertIn("32 active contacts are available; query __contacts", content)
            self.assertIn("most recently active or updated", content)
            self.assertIn("ORDER BY relevance_at DESC", content)
            sample_section = content.split("Sample active contacts", 1)[1].split(
                "Only contact people", 1
            )[0]
            self.assertEqual(sample_section.count("- email:"), 10)
            self.assertIn("recent-conversation@example.com", sample_section)
            self.assertIn("newly-updated@example.com", sample_section)
            self.assertLess(
                sample_section.index("recent-conversation@example.com"),
                sample_section.index("newly-updated@example.com"),
            )
            self.assertNotIn("old-15@example.com", sample_section)

            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT last_conversed_at, relevance_at
                    FROM "__contacts"
                    WHERE normalized_address = 'recent-conversation@example.com';
                    """
                )
                recent_row = cur.fetchone()
                self.assertIsNotNone(recent_row)
                self.assertEqual(recent_row[0], conversation_time.isoformat())
                self.assertEqual(recent_row[1], conversation_time.isoformat())

                cur.execute(
                    """
                    SELECT relevance_at
                    FROM "__contacts"
                    WHERE normalized_address = 'old-15@example.com';
                    """
                )
                omitted_row = cur.fetchone()
                self.assertIsNotNone(omitted_row)
                self.assertEqual(omitted_row[0], old_time.isoformat())
            finally:
                conn.close()

    def test_small_allowlist_stays_fully_inline(self):
        CommsAllowlistEntry.objects.bulk_create(
            [
                CommsAllowlistEntry(
                    agent=self.agent,
                    channel=CommsChannel.EMAIL,
                    address=f"small-{index:02d}@example.com",
                    is_active=True,
                    allow_inbound=True,
                    allow_outbound=True,
                )
                for index in range(3)
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            content = self._render_prompt_content(os.path.join(tmp, "state.db"))

        self.assertNotIn("active contacts are available; query __contacts", content)
        self.assertIn("small-00@example.com", content)
        self.assertIn("small-01@example.com", content)
        self.assertIn("small-02@example.com", content)


@tag("batch_event_processing")
class ContinuationModePromptContextTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="continuation-prompt@example.com",
            email="continuation-prompt@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="ContinuationPromptBA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Continuation Prompt Agent",
            charter="Handle ongoing work",
            browser_use_agent=browser_agent,
        )

    def _render_system_prompt(self, *, is_first_run: bool) -> str:
        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ), patch(
            "api.agent.core.prompt_context.get_llm_config_with_failover",
            return_value=[("endpoint", "openai/gpt-4o-mini", {})],
        ):
            context, _, _ = build_prompt_context_preview(
                self.agent,
                is_first_run=is_first_run,
            )

        return next(message["content"] for message in context if message["role"] == "system")

    def test_prompt_includes_continuation_mode_after_first_run(self):
        system_prompt = self._render_system_prompt(is_first_run=False)

        self.assertIn("## Continuation Mode", system_prompt)
        self.assertIn("Continue the existing work thread", system_prompt)
        self.assertIn("prefer one direct next tool call", system_prompt)
        self.assertIn("If one workstream waits on human input, credentials, auth, or a third party", system_prompt)
        self.assertIn("continue the next unblocked charter/plan item", system_prompt)
        self.assertIn("verify blockers once, then keep moving", system_prompt)

    def test_prompt_omits_continuation_mode_on_first_run(self):
        system_prompt = self._render_system_prompt(is_first_run=True)

        self.assertNotIn("## Continuation Mode", system_prompt)


@tag("batch_event_processing")
class DailyLimitProcessingTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="daily-limit-processing@example.com",
            email="daily-limit-processing@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="DailyLimitProcessingBA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Daily Limit Processing Agent",
            charter="Handle the user's work",
            browser_use_agent=browser_agent,
        )

    @override_settings(GOBII_PROPRIETARY_MODE=True)
    @patch("api.agent.core.event_processing._run_agent_loop", return_value={"total_tokens": 0})
    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    @patch("api.agent.core.event_processing.maybe_schedule_agent_avatar")
    @patch("api.agent.core.event_processing.maybe_schedule_agent_tags")
    @patch("api.agent.core.event_processing.maybe_schedule_mini_description")
    @patch("api.agent.core.event_processing.maybe_schedule_short_description")
    @patch(
        "api.agent.core.event_processing.get_agent_daily_credit_state",
        return_value={
            "hard_limit": Decimal("2"),
            "hard_limit_remaining": Decimal("0"),
            "soft_target": Decimal("1"),
            "soft_target_remaining": Decimal("0"),
            "soft_target_exceeded": True,
            "used": Decimal("2"),
            "next_reset": timezone.now(),
        },
    )
    @patch(
        "api.agent.core.event_processing.TaskCreditService.calculate_available_tasks_for_owner",
        return_value=Decimal("0"),
    )
    def test_process_agent_events_enters_message_only_mode_without_notice(
        self,
        _mock_available,
        _mock_daily_state,
        _mock_short_description,
        _mock_mini_description,
        _mock_agent_tags,
        _mock_agent_avatar,
        mock_run_loop,
    ):
        _process_agent_events_locked(self.agent.id, _DummySpan())

        mock_run_loop.assert_called_once()
        self.assertTrue(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                notes="daily_credit_limit_exhausted",
            ).exists()
        )

    @patch("api.agent.core.period_events.get_redis_client")
    @patch("api.agent.core.event_processing.Analytics.track_event")
    @patch(
        "api.agent.core.event_processing.TaskCreditService.calculate_available_tasks_for_owner",
        return_value=Decimal("10"),
    )
    @patch("api.agent.core.event_processing.get_tool_credit_cost", return_value=Decimal("1"))
    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    def test_hard_limit_analytics_and_system_step_emit_once_per_daily_period(
        self,
        _mock_tool_cost,
        _mock_available,
        mock_track_event,
        mock_get_redis_client,
    ):
        redis_client = MagicMock()
        redis_client.set.side_effect = [True, True, False, False]
        mock_get_redis_client.return_value = redis_client

        for _ in range(2):
            daily_state = {
                "hard_limit": Decimal("2"),
                "hard_limit_remaining": Decimal("0"),
                "soft_target": None,
                "soft_target_remaining": None,
                "soft_target_exceeded": False,
                "used": Decimal("2"),
                "next_reset": timezone.now(),
            }

            result = _ensure_credit_for_tool(
                self.agent,
                "browser_search",
                span=_DummySpan(),
                credit_snapshot={"available": Decimal("10"), "daily_state": daily_state},
            )
            self.assertFalse(result)

        self.assertEqual(mock_track_event.call_count, 1)
        self.assertEqual(
            PersistentAgentSystemStep.objects.filter(
                step__agent=self.agent,
                notes="daily_credit_limit_mid_loop",
            ).count(),
            1,
        )

    @patch("api.agent.core.period_events.get_redis_client")
    def test_daily_event_marker_fails_open_on_cache_error(self, mock_get_redis_client):
        mock_get_redis_client.side_effect = RuntimeError("redis unavailable")
        self.assertTrue(should_emit_daily_agent_event(self.agent.id, DAILY_SOFT_LIMIT_EXCEEDED_EVENT))

    @patch("api.agent.core.burn_control.Analytics.track_event")
    @patch("api.agent.core.burn_control.get_agent_baseline_llm_tier", return_value=AgentLLMTier.PREMIUM)
    @patch("api.agent.core.period_events.get_redis_client")
    def test_burn_rate_step_down_analytics_emit_once_but_override_still_applies(
        self,
        mock_get_redis_client,
        _mock_baseline_tier,
        mock_track_event,
    ):
        redis_client = MagicMock()
        redis_client.set.side_effect = [True, False]
        mock_get_redis_client.return_value = redis_client

        daily_state = {
            "burn_rate_per_hour": Decimal("20"),
            "burn_rate_threshold_per_hour": Decimal("10"),
            "burn_rate_window_minutes": 60,
            "burn_rate_24h_total": Decimal("25"),
            "burn_rate_threshold_24h": Decimal("0"),
        }

        first_action = handle_burn_rate_limit(
            self.agent,
            budget_ctx=None,
            span=_DummySpan(),
            daily_state=daily_state,
        )
        self.assertEqual(first_action, BurnRateAction.STEPPED_DOWN)
        self.assertEqual(get_runtime_tier_override(self.agent), AgentLLMTier.STANDARD)

        clear_runtime_tier_override(self.agent)
        second_action = handle_burn_rate_limit(
            self.agent,
            budget_ctx=None,
            span=_DummySpan(),
            daily_state=daily_state,
        )

        self.assertEqual(second_action, BurnRateAction.STEPPED_DOWN)
        self.assertEqual(get_runtime_tier_override(self.agent), AgentLLMTier.STANDARD)
        self.assertEqual(mock_track_event.call_count, 1)
