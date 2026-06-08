from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.core import prompt_context
from api.models import BrowserUseAgent, CommsAllowlistEntry, CommsChannel, PersistentAgent


@tag("batch_promptree")
class PromptContextSqliteGuidanceTests(SimpleTestCase):
    def test_tool_results_schema_mentions_result_text(self):
        examples = prompt_context._get_sqlite_examples()
        section = examples.split("# __tool_results (special table)", 1)[1].split(
            "# JSON: path from hint", 1
        )[0]
        self.assertIn("result_text", section)
        self.assertIn("analysis_json", section)
        self.assertIn("json_extract(result_json,'$.result')", section)
        self.assertIn("do not invent columns", section)

    def test_examples_require_sqlite_tool_for_database_queries(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("User asks to query SQLite/database/tables", examples)
        self.assertIn("schema proves shape, not result data", examples)

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

    def test_examples_include_contacts_table_schema(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("# __contacts (special table)", examples)
        self.assertIn("normalized_address", examples)
        self.assertIn("status='allowed' AND allow_outbound=1", examples)
        self.assertIn("empty pending request queue", examples)

    def test_examples_discourage_browser_task_completion_polling(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("completed browser task wakes you and adds a `spawn_web_task_result` row", examples)
        self.assertIn("Don't poll __tool_results/__files waiting for browser task completion before that wake-up", examples)

    def test_sqlite_retry_warning_flags_repeated_empty_probes(self):
        warning = prompt_context._build_sqlite_retry_warning(
            [
                (
                    {"sql": "SELECT * FROM __tool_results WHERE result_id='73b1fa'"},
                    '{"results":[{"message":"Query 0 returned 0 rows."}]}',
                ),
                (
                    {"sql": "SELECT grep_context_all(result_text, 'Tomorrow') FROM __tool_results WHERE result_id='73b1fa'"},
                    '{"results":[{"message":"Query 0 returned 0 rows."}]}',
                ),
                (
                    {"sql": "SELECT csv_headers(result_text) FROM __tool_results WHERE result_id='73b1fa'"},
                    '{"results":[{"result":[{"headers":"[\\"New York\\",\\"Forecast\\"]"}]}]}',
                ),
                (
                    {"sql": "SELECT regexp_extract(result_text, 'Hi: (\\\\d+)') FROM __tool_results WHERE result_id='73b1fa'"},
                    '{"results":[{"message":"Query 0 returned 0 rows."}]}',
                ),
            ]
        )

        self.assertIn("Loop warning", warning)
        self.assertIn("73b1fa", warning)

    def test_examples_show_larger_grep_context_window(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("grep_context_all(\n        json_extract(result_json,'$.excerpt'), '<pattern>', 120, 12)", examples)
        self.assertIn("try wider context (200 chars)", examples)

    def test_examples_prefer_shaped_multi_result_queries(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn("multiple_results", examples)
        self.assertIn("working_table", examples)
        self.assertIn("avoid → one result_text fetch per source", examples)

    def test_sqlite_retry_warning_flags_blob_fetch_loops(self):
        warning = prompt_context._build_sqlite_retry_warning(
            [
                ({"sql": "SELECT result_text FROM __tool_results WHERE result_id='a1'"}, "{}"),
                ({"sql": "SELECT result_text FROM __tool_results WHERE result_id='b2'"}, "{}"),
            ]
        )

        self.assertIn("SQLite efficiency warning", warning)
        self.assertIn("one shaped query", warning)


class _PromptSectionCollector:
    def __init__(self):
        self.sections = {}

    def section_text(self, name, text, **_kwargs):
        self.sections[name] = text


class _NoopSpan:
    def set_attribute(self, *_args, **_kwargs):
        return None


@tag("batch_promptree")
class PromptContextContactsGuidanceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            verified=True,
            primary=True,
        )
        self.browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Prompt Contacts Browser",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Prompt Contacts Agent",
            charter="Test contacts guidance.",
            browser_use_agent=self.browser_agent,
        )

    def test_large_allowed_contacts_are_compacted_in_prompt(self):
        CommsAllowlistEntry.objects.bulk_create(
            [
                CommsAllowlistEntry(
                    agent=self.agent,
                    channel=CommsChannel.EMAIL,
                    address=f"person-{idx:02d}@example.com",
                    is_active=True,
                    allow_inbound=True,
                    allow_outbound=True,
                )
                for idx in range(prompt_context.CONTACT_PROMPT_INLINE_LIMIT + 5)
            ]
        )
        collector = _PromptSectionCollector()

        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            prompt_context._ConfigAuthorityResolver(self.agent),
        )

        allowed_contacts = collector.sections["allowed_contacts"]
        self.assertIn("__contacts", allowed_contacts)
        self.assertIn("active contacts are available", allowed_contacts)
        self.assertIn("Sample active contacts", allowed_contacts)
        self.assertIn("person-00@example.com", allowed_contacts)
        self.assertNotIn("person-29@example.com", allowed_contacts)
        self.assertIn("status='allowed' AND allow_outbound=1", allowed_contacts)

    def test_examples_prefer_patch_and_retry_for_named_missing_parameters(self):
        examples = prompt_context._get_sqlite_examples()
        self.assertIn(
            "If an API/tool error explicitly names a missing parameter, patch that parameter and retry before broad search unless the error is ambiguous",
            examples,
        )

    def test_planning_first_run_welcome_ends_planning_before_execution_tools(self):
        guidance = prompt_context._get_planning_first_run_welcome_instruction(
            welcome_target=prompt_context._FirstRunWelcomeTarget(
                channel="web",
                address="web://user/1/agent/test",
                send_tool_name="send_chat_message",
            )
        )

        self.assertIn("call end_planning in the same response as any welcome", guidance)
        self.assertIn("never send a welcome-only", guidance)
        self.assertIn("Do not call http_request", guidance)
