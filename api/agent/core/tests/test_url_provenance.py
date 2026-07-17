import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, tag

from api.agent.core.event_processing import _prepare_tool_batch
from api.agent.core.prompt_context import _get_system_instruction
from api.agent.core.url_provenance import (
    build_delivery_url_inventory,
    extract_http_urls,
    unexpected_delivery_urls,
)
from api.agent.tools.sqlite_batch import _row_url_reporting_note
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


@tag("batch_event_processing")
class UrlProvenanceHelperTests(SimpleTestCase):
    def test_extracts_markdown_html_and_plain_urls_exactly(self):
        text = (
            "[One](https://one.example.test/a?x=1#frag) "
            "<a href='https://two.example.test/b'>Two</a> "
            "https://three.example.test/c."
        )

        self.assertEqual(
            extract_http_urls(text),
            (
                "https://one.example.test/a?x=1#frag",
                "https://two.example.test/b",
                "https://three.example.test/c",
            ),
        )

    def test_delivery_validation_rejects_urls_in_code_and_links(self):
        body = (
            "`https://code.example.test/example` "
            "[Open](https://console.example.test/items/item_42?region=west)"
        )

        self.assertEqual(
            unexpected_delivery_urls(body, {"https://source.example.test/feed"}),
            (
                "https://code.example.test/example",
                "https://console.example.test/items/item_42?region=west",
            ),
        )

    def test_delivery_validation_preserves_exact_query_and_fragment(self):
        allowed = "https://items.example.test/42?view=full#details"

        self.assertEqual(unexpected_delivery_urls(f"[Item]({allowed})", {allowed}), ())
        self.assertEqual(
            unexpected_delivery_urls(
                "[Item](https://items.example.test/42?view=summary#details)",
                {allowed},
            ),
            ("https://items.example.test/42?view=summary#details",),
        )

    def test_sqlite_reporting_note_requires_complete_item_url(self):
        partial_note = _row_url_reporting_note([{"item_url": "/items/42"}])
        complete_note = _row_url_reporting_note(
            [{"item_url": "https://items.example.test/42?view=full"}, {"item_url": None}]
        )

        self.assertEqual(partial_note, "")
        self.assertIn("copy those exact item URLs", complete_note)
        self.assertIn("stay unlinked", complete_note)


@tag("batch_event_processing")
class DeliveryUrlProvenanceIntegrationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="url-provenance@example.com",
            email="url-provenance@example.com",
            password="secret",
        )
        browser_agent = BrowserUseAgent.objects.create(user=user, name="URLProvenanceBA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="URL Provenance Agent",
            charter="Use https://charter.example.test/reference when it is relevant.",
            browser_use_agent=browser_agent,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )

    def test_inventory_includes_system_charter_and_tool_result_urls(self):
        step = PersistentAgentStep.objects.create(agent=self.agent, description="Fetched records")
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name="http_request",
            result=json.dumps(
                {
                    "url": "https://api.example.test/feed",
                    "content": (
                        "item_url=https://items.example.test/42?view=full#details\n"
                        "name=Next item"
                    ),
                }
            ),
        )

        inventory = build_delivery_url_inventory(
            self.agent,
            system_prompt="Setup: https://system.example.test/connect",
        )

        self.assertIn("https://charter.example.test/reference", inventory)
        self.assertIn("https://api.example.test/feed", inventory)
        self.assertIn("https://items.example.test/42?view=full#details", inventory)
        self.assertIn("https://system.example.test/connect", inventory)

    def test_inventory_does_not_trust_urls_synthesized_by_sqlite(self):
        source_url = "https://items.example.test/42"
        constructed_url = "https://items.example.test/99"
        source_step = PersistentAgentStep.objects.create(agent=self.agent, description="Fetched records")
        PersistentAgentToolCall.objects.create(
            step=source_step,
            tool_name="http_request",
            result=json.dumps({"item_url": source_url, "item_id": "99"}),
        )
        derived_step = PersistentAgentStep.objects.create(agent=self.agent, description="Queried records")
        PersistentAgentToolCall.objects.create(
            step=derived_step,
            tool_name="sqlite_batch",
            result=json.dumps({"result": [{"item_url": constructed_url}]}),
        )

        inventory = build_delivery_url_inventory(self.agent)

        self.assertIn(source_url, inventory)
        self.assertNotIn(constructed_url, inventory)

    def test_prepare_batch_blocks_unseen_url_before_delivery(self):
        body = "[Open](https://console.example.test/items/item_42?region=west)"
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "send_chat_message",
                "arguments": json.dumps({"body": body, "will_continue_work": False}),
            },
        }

        with patch(
            "api.agent.core.event_processing.get_agent_daily_credit_state",
            return_value=None,
        ), patch(
            "api.agent.core.event_processing._resolve_tool_for_execution",
            return_value=("send_chat_message", None),
        ):
            prepared = _prepare_tool_batch(
                self.agent,
                tool_calls=[tool_call],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=True,
                attach_completion=lambda _kwargs: None,
                attach_prompt_archive=lambda _step: None,
                delivery_url_inventory=frozenset({"https://source.example.test/feed"}),
            )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__contains="Message delivery blocked",
            ).exists()
        )
        correction = PersistentAgentStep.objects.get(
            agent=self.agent,
            description__contains="Message delivery blocked",
        ).description
        self.assertIn("Do not mention their host, route, ID, or query fragments", correction)
        self.assertIn("No item URL provided", correction)

    def test_system_prompt_has_one_literal_url_rule(self):
        prompt = _get_system_instruction(self.agent, is_first_run=False)

        self.assertEqual(prompt.count("A delivered HTTP(S) URL must already appear verbatim"), 1)
        self.assertNotIn("Link entities from tool-result URLs, never constructed URLs", prompt)
        self.assertNotIn("Preserve row/entity item URLs", prompt)
