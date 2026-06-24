from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.core import prompt_context
from api.models import (
    BrowserUseAgent,
    CommsAllowlistEntry,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)


@tag("batch_promptree")
class PromptContextSqliteGuidanceTests(SimpleTestCase):
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
        config_authority = prompt_context._ConfigAuthorityResolver(self.agent)
        contact_records = prompt_context.build_contacts_snapshot_records(
            self.agent,
            display_name_for_user=prompt_context._build_user_display_name,
            user_can_configure=config_authority.user_can_configure,
        )

        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            config_authority,
            contact_records,
        )

        allowed_contacts = collector.sections["allowed_contacts"]
        self.assertIn("__contacts", allowed_contacts)
        self.assertIn("active contacts are available", allowed_contacts)
        self.assertIn("Sample active contacts", allowed_contacts)
        self.assertIn("person-29@example.com", allowed_contacts)
        self.assertNotIn("person-00@example.com", allowed_contacts)
        self.assertIn("status='allowed' AND allow_outbound=1", allowed_contacts)

    def test_allowed_channels_include_outbound_allowed_contact_channels(self):
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=f"web://agent/{self.agent.id}",
        )
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="ops@example.test",
            is_active=True,
            allow_inbound=True,
            allow_outbound=True,
        )
        CommsAllowlistEntry.objects.create(
            agent=self.agent,
            channel=CommsChannel.SMS,
            address="+15555550123",
            is_active=True,
            allow_inbound=True,
            allow_outbound=False,
        )
        collector = _PromptSectionCollector()
        config_authority = prompt_context._ConfigAuthorityResolver(self.agent)
        contact_records = prompt_context.build_contacts_snapshot_records(
            self.agent,
            display_name_for_user=prompt_context._build_user_display_name,
            user_can_configure=config_authority.user_can_configure,
        )

        prompt_context._build_contacts_block(
            self.agent,
            collector,
            _NoopSpan(),
            config_authority,
            contact_records,
        )

        allowed_channels = collector.sections["allowed_channels"]
        self.assertIn("You can communicate via: email, web.", allowed_channels)
        self.assertNotIn("sms", allowed_channels)
