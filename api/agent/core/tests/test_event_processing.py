"""Tests for event processing continuation decisions."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag
from django.utils import timezone

from api.agent.core.event_processing import (
    _process_agent_events_locked,
    _parse_tool_call_params,
    _normalize_tool_params_unicode_escapes,
    _should_imply_continue,
)
from django.urls import reverse

from api.agent.core.prompt_context import build_prompt_context
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentSystemStep
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
