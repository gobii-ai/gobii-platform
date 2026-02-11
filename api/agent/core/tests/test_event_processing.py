"""Tests for event processing continuation decisions."""

from django.test import SimpleTestCase, tag

from api.agent.core.event_processing import (
    _normalize_tool_params_unicode_escapes,
    _should_imply_continue,
)


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

    def test_open_kanban_with_natural_continuation_keeps_going(self):
        result = _should_imply_continue(
            has_canonical_continuation=False,
            has_other_tool_calls=False,
            has_explicit_sleep=False,
            has_open_kanban_work=True,
            has_natural_continuation_signal=True,
        )

        self.assertTrue(result)

    def test_open_kanban_without_continuation_signal_stops(self):
        result = _should_imply_continue(
            has_canonical_continuation=False,
            has_other_tool_calls=False,
            has_explicit_sleep=False,
            has_open_kanban_work=True,
            has_natural_continuation_signal=False,
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
