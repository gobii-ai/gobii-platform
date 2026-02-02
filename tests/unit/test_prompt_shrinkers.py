from django.test import TestCase, tag

from api.agent.core.prompt_shrinkers import structured_shrinker


@tag("batch_promptree")
class StructuredShrinkerTests(TestCase):
    """Test suite for the structured shrinker."""

    def test_structured_shrinker_preserves_code_fences_and_tokens(self):
        text = "\n".join(
            [
                "Header line",
                "```sql",
                "SELECT * FROM __tool_results WHERE result_id='abc';",
                "```",
                "Footer line",
            ]
        )
        shrunk = structured_shrinker(text, 0.2)

        self.assertEqual(shrunk.count("```"), 2)
        self.assertIn("__tool_results", shrunk)

    def test_structured_shrinker_keeps_head_and_tail(self):
        text = "\n".join(f"line {i}" for i in range(40))
        shrunk = structured_shrinker(text, 0.15)

        self.assertTrue(shrunk.startswith("line 0"))
        self.assertIn("line 39", shrunk)
        self.assertIn("LINES TRUNCATED", shrunk)

    def test_structured_shrinker_pretty_prints_json(self):
        payload = {
            "alpha": "one",
            "beta": "__tool_results",
            "gamma": list(range(50)),
            "delta": {"nested": "value"},
        }
        text = (
            "{"
            "\"alpha\":\"one\","
            "\"beta\":\"__tool_results\","
            "\"gamma\":["
            + ",".join(str(n) for n in range(50))
            + "],"
            "\"delta\":{\"nested\":\"value\"}"
            "}"
        )
        shrunk = structured_shrinker(text, 0.1)

        self.assertIn("__tool_results", shrunk)
        self.assertIn("LINES TRUNCATED", shrunk)
