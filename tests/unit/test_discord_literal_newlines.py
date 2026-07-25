"""Outbound Discord messages must not render literal backslash-n (#228, #289).

A double-escaped body arrives as the two characters backslash and n, which Discord renders
verbatim. Repair the unambiguous structural cases only: a lone \\n being discussed as text, such as
a bug report about this very defect, must survive untouched.
"""
from __future__ import annotations

from django.test import SimpleTestCase, tag

from api.agent.tools.charter_text import repair_structural_literal_newlines


@tag("batch_agent_chat")
class LiteralNewlineRepairTests(SimpleTestCase):
    def test_repairs_both_halves_of_a_paragraph_break(self):
        """The original fix repaired the first of a pair and stranded the second."""
        body = "**#314 logged**\\n\\n**Status:** minor\\n\\nAndrew reports glitchy UI."

        repaired, count, remaining = repair_structural_literal_newlines(body)

        self.assertEqual(remaining, 0)
        self.assertEqual(count, 4)
        self.assertEqual(
            repaired, "**#314 logged**\n\n**Status:** minor\n\nAndrew reports glitchy UI."
        )

    def test_repairs_a_run_longer_than_two(self):
        repaired, _, remaining = repair_structural_literal_newlines("a\\n\\n\\nb")

        self.assertEqual(remaining, 0)
        self.assertEqual(repaired, "a\n\n\nb")

    def test_repairs_before_a_bullet_list(self):
        repaired, _, remaining = repair_structural_literal_newlines("Blockers:\\n- one\\n- two")

        self.assertEqual(remaining, 0)
        self.assertEqual(repaired, "Blockers:\n- one\n- two")

    def test_repairs_before_a_heading(self):
        repaired, _, remaining = repair_structural_literal_newlines("Intro\\n## Heading")

        self.assertEqual(remaining, 0)
        self.assertEqual(repaired, "Intro\n## Heading")

    def test_leaves_a_lone_newline_being_discussed_as_text(self):
        """A bug report about literal newlines must not have its own example rewritten."""
        body = "Agent output displayed literal `\\n` character sequences instead of breaks."

        repaired, count, remaining = repair_structural_literal_newlines(body)

        self.assertEqual(repaired, body)
        self.assertEqual(count, 0)
        self.assertEqual(remaining, 1)

    def test_leaves_prose_mentions_alone(self):
        body = "I sent literal `\\n` text instead of real line breaks."

        repaired, count, _ = repair_structural_literal_newlines(body)

        self.assertEqual(repaired, body)
        self.assertEqual(count, 0)

    def test_leaves_real_newlines_untouched(self):
        body = "Already fine.\n\n## Heading\n- one"

        repaired, count, remaining = repair_structural_literal_newlines(body)

        self.assertEqual(repaired, body)
        self.assertEqual(count, 0)
        self.assertEqual(remaining, 0)

    def test_handles_empty_and_none(self):
        self.assertEqual(repair_structural_literal_newlines(None), ("", 0, 0))
        self.assertEqual(repair_structural_literal_newlines(""), ("", 0, 0))
