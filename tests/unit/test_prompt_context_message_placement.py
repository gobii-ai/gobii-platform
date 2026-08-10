from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from unittest.mock import patch

from api.agent.core import prompt_context
from api.agent.core.prompt_context import build_prompt_context, build_prompt_context_preview
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)

User = get_user_model()


@tag("batch_promptree")
class PromptContextSqlitePlacementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="prompt_sqlite@example.com",
            email="prompt_sqlite@example.com",
            password="secret",
        )
        self.browser_agent = BrowserUseAgent.objects.create(user=self.user, name="PromptSQLiteBA")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="PromptSQLiteAgent",
            charter="Test sqlite guidance placement",
            browser_use_agent=self.browser_agent,
        )

    def test_history_compaction_is_dispatched_after_prompt_archive(self):
        events = []

        def archive_prompt(*_args, **_kwargs):
            events.append("archive")
            return None

        def enqueue_compaction(**_kwargs):
            events.append("enqueue")
            return True

        with patch(
            "api.agent.core.prompt_context._archive_prompt_render",
            side_effect=archive_prompt,
        ), patch(
            "api.agent.core.prompt_context.enqueue_history_compaction",
            side_effect=enqueue_compaction,
        ), patch(
            "api.agent.core.compaction.ensure_comms_compacted",
        ) as ensure_comms_mock, patch(
            "api.agent.core.step_compaction.ensure_steps_compacted",
        ) as ensure_steps_mock:
            build_prompt_context(self.agent)

        self.assertEqual(events, ["archive", "enqueue"])
        ensure_comms_mock.assert_not_called()
        ensure_steps_mock.assert_not_called()

    def test_prompt_preview_does_not_dispatch_history_compaction(self):
        with patch("api.agent.core.prompt_context.enqueue_history_compaction") as enqueue_mock:
            build_prompt_context_preview(self.agent)

        enqueue_mock.assert_not_called()

    def test_sqlite_guidance_only_in_system_message(self):
        sqlite_guidance = prompt_context._get_sqlite_guidance()

        with patch("api.agent.core.prompt_context.enqueue_history_compaction") as enqueue_mock:
            context, _, _ = build_prompt_context(self.agent)

        enqueue_mock.assert_called_once_with(
            agent=self.agent,
            routing_profile=None,
            eval_run_id=None,
        )

        system_message = next(message for message in context if message["role"] == "system")
        user_message = next(message for message in context if message["role"] == "user")

        self.assertEqual(system_message["content"].count(sqlite_guidance), 1)
        self.assertNotIn(sqlite_guidance, user_message["content"])
        all_contents = "\n".join(message["content"] for message in context)
        self.assertEqual(all_contents.count(sqlite_guidance), 1)
        self.assertIn("<sqlite_guidance>", system_message["content"])
        self.assertIn("</sqlite_guidance>", system_message["content"])
        self.assertIn("Named tables hold truth/logic", sqlite_guidance)
        self.assertIn("Results do not update them", sqlite_guidance)
        self.assertIn("counts, joins, gaps, ranks", sqlite_guidance)
        self.assertIn("never dump history or requery their IDs", sqlite_guidance)
        self.assertIn("Named enabled tool: call it directly, never search", system_message["content"])
        self.assertIn("Keep chat/outreach light. For finite sets", system_message["content"])
        self.assertIn("## Link References (CRITICAL)", system_message["content"])
        self.assertIn("Use one supplied destination", system_message["content"])
        self.assertIn("otherwise an exact supplied raw URL stays", system_message["content"])
        self.assertIn("Never put a URL after `$[link:`", system_message["content"])
        self.assertIn("Source/feed tokens link only themselves", system_message["content"])
        self.assertIn("Link token-backed entity names", system_message["content"])
        self.assertIn("For 3+ comparable items, use one table", system_message["content"])
        self.assertIn("resolve/source each requested field", system_message["content"])
        self.assertIn("grouped discovery isn't coverage", system_message["content"])
        self.assertIn("separate sourced unavailability from research gaps", system_message["content"])
        self.assertIn("The agent settings UI is a single page", all_contents)
        self.assertIn("Do not invent subpage links", all_contents)
        self.assertIn("asks except finite sets", system_message["content"])
        self.assertIn("deep/exhaustive research and finite-set coverage", system_message["content"])
        self.assertIn("batch gaps, follow up misses, and reconcile coverage", system_message["content"])
        self.assertIn("never repeat a successful URL/query", system_message["content"])

    def test_active_plan_precedes_charter_and_charter_precedes_schedule(self):
        self.agent.charter = "DURABLE CHARTER CACHE ANCHOR"
        self.agent.schedule = "0 9 * * 1"
        self.agent.save(update_fields=["charter", "schedule", "updated_at"])

        with patch("api.agent.core.prompt_context.enqueue_history_compaction"):
            context, _, _ = build_prompt_context(self.agent)

        user_content = next(message["content"] for message in context if message["role"] == "user")
        self.assertLess(
            user_content.index("<current_plan>"),
            user_content.index("DURABLE CHARTER CACHE ANCHOR"),
        )
        self.assertLess(
            user_content.index("DURABLE CHARTER CACHE ANCHOR"),
            user_content.index("<schedule>"),
        )

    def test_source_model_warning_uses_only_latest_process_cycle(self):
        old_cycle = PersistentAgentStep.objects.create(agent=self.agent, description="Process events")
        PersistentAgentSystemStep.objects.create(
            step=old_cycle,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )
        old_source = PersistentAgentStep.objects.create(agent=self.agent, description="old source")
        PersistentAgentToolCall.objects.create(
            step=old_source,
            tool_name="http_request",
            tool_params={"url": "https://old.example.test"},
            status="complete",
        )
        current_cycle = PersistentAgentStep.objects.create(agent=self.agent, description="Process events")
        PersistentAgentSystemStep.objects.create(
            step=current_cycle,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

        self.assertEqual(prompt_context._get_unreconciled_source_model_warning(self.agent), "")

        source = PersistentAgentStep.objects.create(agent=self.agent, description="current source")
        PersistentAgentToolCall.objects.create(
            step=source,
            tool_name="http_request",
            tool_params={"url": "https://crm.example.test/account"},
            status="complete",
        )
        model_read = PersistentAgentStep.objects.create(agent=self.agent, description="stale model read")
        PersistentAgentToolCall.objects.create(
            step=model_read,
            tool_name="sqlite_batch",
            tool_params={"sql": "SELECT * FROM accounts WHERE account_id='acct-1'"},
            status="complete",
        )

        self.assertIn(
            "not reconciled",
            prompt_context._get_unreconciled_source_model_warning(self.agent),
        )

        reconcile = PersistentAgentStep.objects.create(agent=self.agent, description="model reconciliation")
        PersistentAgentToolCall.objects.create(
            step=reconcile,
            tool_name="sqlite_batch",
            tool_params={
                "sql": "UPDATE accounts SET stage=(SELECT json_extract(result_json,'$.stage') "
                "FROM __tool_results) WHERE account_id='acct-1'"
            },
            status="complete",
        )

        warning = prompt_context._get_unreconciled_source_model_warning(self.agent)
        self.assertIn("Fresh source evidence is reconciled", warning)
        self.assertIn("still-unread updated table(s): accounts", warning)
        self.assertNotIn("not reconciled", warning)
        post_update_read = PersistentAgentStep.objects.create(agent=self.agent, description="fresh model read")
        PersistentAgentToolCall.objects.create(
            step=post_update_read,
            tool_name="sqlite_batch",
            tool_params={"sql": "SELECT stage FROM accounts WHERE account_id='acct-1'"},
            status="complete",
        )

        self.assertEqual(prompt_context._get_unreconciled_source_model_warning(self.agent), "")
