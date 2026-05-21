"""Tests for event processing continuation decisions."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag
from django.utils import timezone

from api.agent.core.event_processing import (
    _contact_permission_params_from_misrouted_human_input,
    _process_agent_events_locked,
    _is_warning_status,
    _looks_like_blocking_human_input_request,
    _normalize_tool_params,
    _parse_tool_call_params,
    _sanitize_tool_name,
    _should_infer_message_tool_continuation,
    _should_imply_continue,
)
from django.urls import reverse

from api.agent.core.prompt_context import build_prompt_context, build_prompt_context_preview
from api.agent.peer_comm import PeerMessagingError
from api.agent.tools.peer_dm import execute_send_agent_message
from api.agent.tools.tool_manager import _normalize_tool_params_unicode_escapes
from api.agent.tools.web_chat_sender import _looks_like_routine_progress_message
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentSystemStep, SmsContactPurpose
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

    def test_optional_tweak_question_is_not_blocking_human_input(self):
        self.assertFalse(
            _looks_like_blocking_human_input_request(
                "I'll get the RSS feed parsed and the schedule wired up now. "
                "Any tweaks before I lock this in? Otherwise I'm off and running!"
            )
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
            "The data is in. Let me run the final analysis query and deliver the recommendation",
            "The `plan_candidates` table is populated with 8 rows. Now let me query it for the best plan.",
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

    @override_settings(PUBLIC_SITE_URL="https://example.com")
    def test_prompt_includes_daily_limit_message_only_links(self):
        daily_state = {
            "hard_limit": Decimal("2"),
            "hard_limit_remaining": Decimal("0"),
            "soft_target": Decimal("1"),
            "soft_target_remaining": Decimal("0"),
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
        content = user_message["content"]
        self.assertIn("DAILY HARD LIMIT MODE", content)
        self.assertIn(settings_url, content)
        self.assertIn(f"double {double_limit_url_prefix}?token=", content)
        self.assertIn(f"unlimited {unlimited_limit_url_prefix}?token=", content)
        self.assertIn("Once the user raises the limit, you can continue the task.", content)


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
        self.assertIn("You are continuing an existing work thread, not starting a new task.", system_prompt)
        self.assertIn("Prefer one direct next tool call over broad reassessment.", system_prompt)

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
