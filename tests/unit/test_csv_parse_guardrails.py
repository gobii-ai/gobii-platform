import json

from django.test import SimpleTestCase, tag

from api.agent.tools.sqlite_guardrails import _csv_column, _csv_parse


@tag("batch_sqlite")
class CsvParseGuardrailsTests(SimpleTestCase):
    def test_csv_parse_detects_sep_prefix(self):
        text = "sep=;\nname;age\nAlice;30\nBob;25"

        parsed = json.loads(_csv_parse(text))

        self.assertEqual(parsed[0]["name"], "Alice")
        self.assertEqual(parsed[1]["age"], "25")
        values = json.loads(_csv_column(text, 1))
        self.assertEqual(values, ["30", "25"])

    def test_csv_parse_dedupes_headers(self):
        text = "name,name\nAlice,Bob"

        parsed = json.loads(_csv_parse(text))

        self.assertEqual(parsed[0]["name"], "Alice")
        self.assertEqual(parsed[0]["name_2"], "Bob")

    def test_csv_parse_no_header(self):
        text = "a\tb\nc\td"

        parsed = json.loads(_csv_parse(text, 0))

        self.assertEqual(parsed[0], ["a", "b"])
        self.assertEqual(parsed[1], ["c", "d"])
