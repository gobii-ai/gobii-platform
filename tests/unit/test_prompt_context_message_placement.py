from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from unittest.mock import patch

from api.agent.core import prompt_context
from api.agent.core.prompt_context import build_prompt_context
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

    def test_sqlite_guidance_only_in_system_message(self):
        sqlite_guidance = prompt_context._get_sqlite_guidance()

        with patch("api.agent.core.prompt_context.ensure_steps_compacted"), patch(
            "api.agent.core.prompt_context.ensure_comms_compacted"
        ):
            context, _, _ = build_prompt_context(self.agent)

        system_message = next(message for message in context if message["role"] == "system")
        user_message = next(message for message in context if message["role"] == "user")

        self.assertEqual(system_message["content"].count(sqlite_guidance), 1)
        self.assertNotIn(sqlite_guidance, user_message["content"])
        all_contents = "\n".join(message["content"] for message in context)
        self.assertEqual(all_contents.count(sqlite_guidance), 1)
        self.assertIn("<sqlite_guidance>", system_message["content"])
        self.assertIn("</sqlite_guidance>", system_message["content"])
        self.assertIn("Named tables are the world model", sqlite_guidance)
        self.assertIn("query them, not memory", sqlite_guidance)
        self.assertIn("use exact SQL for sets/counts/ranking", sqlite_guidance)
        self.assertIn("Keep chat/outreach light. For finite sets", system_message["content"])
        self.assertIn("## Link References (CRITICAL)", system_message["content"])
        self.assertIn("the raw URL is evidence", system_message["content"])
        self.assertIn("adjacent token is only a display/fetch handle", system_message["content"])
        self.assertIn("Items without a token stay plain", system_message["content"])
        self.assertIn("source/feed tokens link only themselves", system_message["content"])
        self.assertIn("A report is unfinished while a token-backed entity name is plain", system_message["content"])
        self.assertIn("resolve/source each requested field", system_message["content"])
        self.assertIn("grouped discovery isn't coverage", system_message["content"])
        self.assertIn("separate sourced unavailability from research gaps", system_message["content"])
        self.assertIn("The agent settings UI is a single page", all_contents)
        self.assertIn("Do not invent subpage links", all_contents)
        self.assertIn("asks except finite sets", system_message["content"])
        self.assertIn("deep/exhaustive research and finite-set coverage", system_message["content"])
        self.assertIn("batch gaps, follow up misses, and reconcile coverage", system_message["content"])
        self.assertIn("never repeat a successful URL/query", system_message["content"])

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
