from django.test import SimpleTestCase, tag

from api.agent.core import prompt_context


@tag("batch_promptree")
class PromptContextSqliteGuidanceTests(SimpleTestCase):
    def test_tool_results_schema_mentions_result_text(self):
        examples = prompt_context._get_sqlite_examples()
        section = examples.split("# __tool_results (special table)", 1)[1].split(
            "# JSON: path from hint", 1
        )[0]
        self.assertIn("result_text", section)
        self.assertIn("analysis_json", section)
        self.assertIn("do not invent columns", section)

    def test_csv_parsing_requires_inspection_and_result_text(self):
        examples = prompt_context._get_sqlite_examples()
        csv_section = examples.split("## CSV Parsing", 1)[1].split(
            "## Data Cleaning Functions", 1
        )[0]
        self.assertIn("inspect before parsing", csv_section)
        self.assertIn("result_text", csv_section)
        self.assertIn("path_from_hint", csv_section)

    def test_examples_include_messages_table_schema(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("# __messages (special table)", examples)
        self.assertIn("attachment_paths_json", examples)
        self.assertIn("rejected_attachments_json", examples)
        self.assertIn("latest_status", examples)

    def test_examples_include_files_table_schema(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("# __files (special table; metadata only)", examples)
        self.assertIn("recent_files", examples)
        self.assertIn("metadata only", examples)

    def test_examples_discourage_browser_task_completion_polling(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("Browser task completions are pushed into unified history", examples)
        self.assertIn("don't poll __tool_results/__files waiting for them", examples)
