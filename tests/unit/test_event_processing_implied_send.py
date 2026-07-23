"""Tests for implied send behavior in event processing."""
from decimal import Decimal
from datetime import timedelta
import json
from unittest.mock import MagicMock, patch

import sqlparse
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.agent.core import event_processing as ep
from api.agent.comms.routing import (
    bind_inbound_routing_scope,
    capture_inbound_routing_scope,
    get_bound_inbound_routing_scope,
    reset_inbound_routing_scope,
)
from api.agent.core.internal_reasoning import INTERNAL_REASONING_PREFIX
from api.agent.core.prompt_context import _get_implied_send_context
from api.agent.core.web_streaming import resolve_web_stream_target
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.models import (
    AgentCollaborator,
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
    PersistentAgentCronTrigger,
    PersistentAgentWebSession,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    UserQuota,
    build_web_agent_address,
    build_web_user_address,
)
from api.services.web_sessions import start_web_session


@tag("batch_event_processing_credits")
class ImpliedSendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="implied@example.com",
            email="implied@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        self.task_credit_patcher = patch(
            "api.models.TaskCreditService.check_and_consume_credit_for_owner",
            return_value={"success": True, "credit": None},
        )
        self.task_credit_patcher.start()
        self.addCleanup(self.task_credit_patcher.stop)
        self.follow_up_patcher = patch("api.agent.core.event_processing._schedule_agent_follow_up")
        self.follow_up_patcher.start()
        self.addCleanup(self.follow_up_patcher.stop)

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="browser-agent-for-implied-send",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Implied Send Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )

    def test_humanized_message_normalization_covers_built_in_delivery_channels(self):
        for tool_name, params in (
            ("send_chat_message", {"body": "Quick update—this is done."}),
            ("send_sms", {"body": "Quick update - this is done."}),
            ("send_email", {"subject": "Update—done", "mobile_first_html": "<p>Done.</p>"}),
            ("send_discord_message", {"message": "Quick update—this is done."}),
        ):
            with self.subTest(tool_name=tool_name):
                result = ep._normalize_humanized_message_params(tool_name, params)
                self.assertNotIn("—", str(result))
                self.assertNotIn("--", str(result))
                self.assertNotIn(" - ", str(result))

        self.assertEqual(
            ep._normalize_humanized_message_params(
                "send_email",
                {
                    "subject": "Quick update",
                    "mobile_first_html": "<p>A natural, low-pressure note.</p>",
                },
            ),
            {
                "subject": "Quick update",
                "mobile_first_html": "<p>A natural, low-pressure note.</p>",
            },
        )

    def test_discord_uses_the_same_delivery_and_reply_semantics(self):
        inbound = MagicMock()
        inbound.conversation.is_peer_dm = False
        inbound.conversation.channel = CommsChannel.DISCORD

        self.assertIn("send_discord_message", ep.MESSAGE_TOOL_NAMES)
        self.assertEqual(ep.MESSAGE_TOOL_BODY_KEYS["send_discord_message"], "message")
        self.assertEqual(ep._same_channel_reply_tool_name(inbound), "send_discord_message")
        self.assertTrue(
            ep._message_tool_is_terminal(
                "send_discord_message",
                {"message": "The report is ready.", "will_continue_work": False},
            )
        )

    def test_discord_research_requires_kickoff_before_first_work_call(self):
        reason = ep._deep_work_update_gate_reason(
            "Please research whether this restriction is temporary.",
            ["mcp_brightdata_search_engine"],
            prior_work_count=0,
            prior_update_count=0,
            batch_has_progress_update=False,
            require_kickoff=True,
        )

        self.assertEqual(reason, "kickoff")

    def test_ordinary_web_research_does_not_trigger_deep_work_gate(self):
        reason = ep._deep_work_update_gate_reason(
            "Please research whether this restriction is temporary.",
            ["mcp_brightdata_search_engine"],
            prior_work_count=0,
            prior_update_count=0,
            batch_has_progress_update=False,
        )

        self.assertIsNone(reason)

    def test_explicit_research_detection_excludes_negated_requests(self):
        self.assertTrue(ep._is_explicit_research_request("Please research this account restriction."))
        self.assertTrue(ep._is_explicit_research_request("Could you look into this?"))
        self.assertTrue(ep._is_explicit_research_request("Find out why the visibility changed."))
        self.assertFalse(ep._is_explicit_research_request("No need to research this; use the supplied answer."))
        self.assertFalse(ep._is_explicit_research_request("Don't figure this out; just use the supplied note."))

    def test_run_setup_resets_inbound_scope_when_prompt_cache_setup_fails(self):
        with patch.object(ep, "PromptRunCache", side_effect=RuntimeError("cache setup failed")), \
             self.assertRaisesRegex(RuntimeError, "cache setup failed"):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertIsNone(get_bound_inbound_routing_scope(self.agent))

    def test_search_tools_is_a_discovery_barrier_for_same_batch_work(self):
        search_call = {"function": {"name": "search_tools", "arguments": '{"query":"meta gobii"}'}}
        research_call = {"function": {"name": "mcp_brightdata_search_engine", "arguments": "{}"}}
        http_call = {
            "function": {
                "name": "http_request",
                "arguments": '{"method":"GET","url":"https://example.test/data"}',
            }
        }
        sqlite_call = {"function": {"name": "sqlite_batch", "arguments": '{"sql":"SELECT * FROM accounts"}'}}
        write_call = {"function": {"name": "mcp_slack_send_message", "arguments": "{}"}}

        self.assertEqual(
            ep._defer_tool_calls_behind_dependencies([search_call, research_call]),
            [search_call],
        )
        self.assertEqual(ep._defer_tool_calls_behind_dependencies([research_call]), [research_call])
        self.assertEqual(
            ep._defer_tool_calls_behind_dependencies([http_call, sqlite_call]),
            [http_call],
        )
        self.assertEqual(ep._defer_tool_calls_behind_dependencies([research_call, sqlite_call]), [research_call])
        self.assertEqual(ep._defer_tool_calls_behind_dependencies([write_call, sqlite_call]), [sqlite_call])

    def test_sqlite_approval_read_precedes_http_write(self):
        approval_read = {
            "function": {
                "name": "sqlite_batch",
                "arguments": '{"sql":"SELECT approved FROM outreach_approvals WHERE prospect_id = 42"}',
            }
        }
        http_write = {
            "function": {
                "name": "http_request",
                "arguments": '{"method":"POST","url":"https://api.example.test/messages"}',
            }
        }

        self.assertEqual(
            ep._defer_tool_calls_behind_dependencies([http_write, approval_read]),
            [approval_read],
        )

    def test_sqlite_approval_read_precedes_unrecognized_mcp_write(self):
        approval_read = {
            "function": {
                "name": "sqlite_batch",
                "arguments": '{"sql":"SELECT approved FROM release_approvals WHERE pr_number = 1341"}',
            }
        }
        merge_call = {
            "function": {
                "name": "mcp_github_merge_pull_request",
                "arguments": '{"pull_number":1341}',
            }
        }

        self.assertEqual(
            ep._defer_tool_calls_behind_dependencies([merge_call, approval_read]),
            [approval_read],
        )

    def test_feedback_classifier_preserves_real_world_intent(self):
        durable_feedback = (
            "That sounded automated. Stop writing like a template.",
            "You sound robotic.",
            "Lowercase is too informal.",
            "No more em dashes.",
            "Don't use em dashes.",
            "you should have github secrets that allow you to use github.",
            "You already have credentials for that command-line workflow.",
            "The agent should be able to use the configured environment variables.",
            "Please stop - writing like a template.",
            "Please stop always writing like a template.",
            "Stop repeatedly sending generic replies.",
            "I prefer comparison tables and bullet takeaways. The narrative format is hard to scan.",
            "Going forward, show source links in reports.",
            "Going forward, keep responses short.",
            "From now on, make every response concise.",
            "Going forward, keep replies short.",
            "I prefer short responses.",
            "Do I have to request links each time? Make that a rule.",
            "yeah these play by play updates arent useful. just come back with what changed + blockers + next move",
            "That felt kind of stiff.",
            "this is great actually. a genuine observation can be the whole opener sometimes, no pitch needed",
            "These updates aren't useful. Never store this customer secret.",
            "For this batch, put security first. Going forward, never use em dashes.",
            "Don't save this temporary point; going forward, these updates aren't useful.",
        )
        for text in durable_feedback:
            with self.subTest(durable=text):
                self.assertTrue(ep._user_text_is_direct_correction(text))
        quote = "Curious what you think the biggest gap is right now."
        self.assertTrue(ep._user_text_is_direct_correction(
            f'"{quote}" this is not so good. it makes the other person do the work',
            prior_outbound_text=quote,
        ))

        one_off_or_content = (
            "Remember, the deadline is Friday.",
            "I prefer Tuesday for this meeting.",
            "Always use the Q3 figures in this report.",
            "Stop sending this email.",
            "Stop using this source for the report.",
            "Send a reply that sounds robotic for a demo.",
            "This is great news about Acme's strategy.",
            "Those customer messages are not helpful; categorize the complaints.",
            "This report is bad. Find a better source.",
            "Your answer was wrong: the renewal date is June 2.",
            "Explain why customers never share passwords by email.",
            "Don't browse. Just answer the question.",
            "Never send this confidential email.",
            "You should have a nice day.",
            "That is a better strategy for Acme.",
            "You should have access to the CRM tools; export the leads.",
            "Continue daily sourcing until the recruiter instructs you to pause, stop, change search criteria, change format, or close the role.",
            "Constraint: do not imply a prior relationship or make unsupported claims. Send the email now.",
        )
        for text in one_off_or_content:
            with self.subTest(not_durable=text):
                self.assertFalse(ep._user_text_is_direct_correction(text))

    def test_feedback_classifier_extracts_scoped_rules_and_tasks(self):
        lasting_cases = (
            ("For this renewal only put legal first, going forward send outcomes only.", ("going forward send outcomes only.",)),
            ("That sounded automated. Stop using templates. Rewrite it naturally.", ("That sounded automated.", "Stop using templates.")),
            ("That sounded automated. Edit the spreadsheet to add source links.", ("That sounded automated.",)),
            (
                "Your reports are too generic. Compare the four companies now. Going forward, use a compact table for portfolio comparisons.",
                ("Your reports are too generic.", "Going forward, use a compact table for portfolio comparisons."),
            ),
            ("For this batch, put security first. Always include source links.", ("Always include source links.",)),
            ("Do not save this feedback. Going forward, these updates are not useful.", ("Going forward, these updates are not useful.",)),
            ("Never send this confidential email. Going forward, never share customer secrets.", ("Going forward, never share customer secrets.",)),
        )
        for text, expected in lasting_cases:
            with self.subTest(lasting=text):
                self.assertEqual(ep._analyze_feedback_turn(text).lasting, expected)

        separate_tasks = (
            ("Your reports are too generic. Open the portfolio now.", "Your reports are too generic."),
            ("That sounded robotic and find three prospects.", "That sounded robotic"),
            ("These updates are not useful and pause the schedule.", "These updates are not useful"),
            ("Your reports are too generic and research the four companies now.", "Your reports are too generic"),
            ("That email was too formal and email the rewrite to Sarah.", "That email was too formal"),
            ("Your report was too generic and export it as CSV.", "Your report was too generic"),
        )
        for text, expected_feedback in separate_tasks:
            with self.subTest(separate_task=text):
                analysis = ep._analyze_feedback_turn(text, "Here is the prior report.")
                self.assertEqual(analysis.lasting, (expected_feedback,))
                self.assertTrue(analysis.separate_task)
                self.assertFalse(analysis.feedback_only)

        direct_rewrites = (
            "That sounded robotic and rewrite it naturally.",
            "That sounded robotic and shorten it.",
        )
        for text in direct_rewrites:
            with self.subTest(direct_rewrite=text):
                analysis = ep._analyze_feedback_turn(text)
                self.assertEqual(analysis.lasting, ("That sounded robotic",))
                self.assertTrue(analysis.direct_reply_task)
                self.assertFalse(analysis.separate_task)

        mixed_rewrite_tasks = (
            "That sounded robotic. Rewrite it, then find three prospects.",
            "That sounded robotic. Rewrite it. Email it to Sarah.",
        )
        for text in mixed_rewrite_tasks:
            with self.subTest(rewrite_then_task=text):
                analysis = ep._analyze_feedback_turn(text)
                self.assertTrue(analysis.direct_reply_task)
                self.assertTrue(analysis.separate_task)

    def test_feedback_classifier_distinguishes_feedback_from_temporary_and_domain_text(self):
        feedback_only = (
            ("going forward, these play by play updates arent useful. just come back with what changed + blockers + next move. routine followups are for Morgan, only pull me in on a real blocker", ""),
            ('"Curious what you think the biggest gap is right now." this is not so good. it makes the other person do the work', "Curious what you think the biggest gap is right now."),
            ("Great that you found leads, but you did not include links. Do I have to ask each time? Can we make that a rule?", ""),
            ("Going forward, keep the updates short and include source links.", ""),
        )
        for text, prior in feedback_only:
            with self.subTest(feedback_only=text):
                analysis = ep._analyze_feedback_turn(text, prior)
                self.assertTrue(analysis.feedback_only)
                self.assertFalse(analysis.separate_task)

        transient_feedback = (
            "That felt stiff; don't update your instructions.",
            "That sounded robotic. Don't save this.",
            "For this email only, do not use em dashes.",
            "For this response, don't use headings.",
            "For this message, that tone is too formal.",
            "For this batch, never use headings.",
            "Never store this customer secret.",
            "Never persist this API key.",
            "Never send this confidential email.",
            "Going forward, do not save this feedback. That sounded stiff.",
        )
        for text in transient_feedback:
            with self.subTest(transient=text):
                analysis = ep._analyze_feedback_turn(text)
                self.assertTrue(analysis.transient_only)
                self.assertFalse(ep._user_text_is_direct_correction(text))
                if text.startswith("Never"):
                    self.assertEqual(analysis.lasting, ())

        for text in (
            "For this customer only, that tone is too formal.",
            "For this channel only, those updates aren't useful.",
        ):
            with self.subTest(durable_context_scope=text):
                analysis = ep._analyze_feedback_turn(text)
                self.assertFalse(analysis.transient_only)
                self.assertEqual(analysis.lasting, (text,))

        task_turns = (
            "Only this batch, these long updates aren't useful. Pause your schedule until Friday.",
            "These updates aren't useful. Going forward send only blockers. Pause your schedule until Friday.",
            "You sound robotic, please find three prospects.",
        )
        for text in task_turns:
            with self.subTest(feedback_plus_task=text):
                self.assertFalse(ep._analyze_feedback_turn(text).feedback_only)

    def test_structured_charter_patch_compiles_to_one_safe_sqlite_update(self):
        old = "Owner's rule;\nDROP TABLE notes;"
        new = "Owner's clearer rule;\nSELECT * FROM notes;"
        call = {
            "id": "patch",
            "type": "function",
            "function": {
                "name": "sqlite_batch",
                "arguments": json.dumps({
                    "target_charter_text": old,
                    "replacement_charter_text": new,
                }),
            },
        }

        compiled = ep._compile_charter_patch_tool_call(call)
        params = json.loads(compiled["function"]["arguments"])

        self.assertEqual(len(sqlparse.split(params["sql"])), 1)
        self.assertIn("Owner''s rule", params["sql"])
        self.assertIn("Owner''s clearer rule", params["sql"])
        self.assertIs(params["will_continue_work"], True)

        call["function"]["arguments"] = json.dumps({
            "target_charter_text": "Research prospects.",
            "replacement_charter_text": "Research prospects. Include verified source links.",
        })
        compiled = ep._compile_charter_patch_tool_call(call, "Research prospects.")
        self.assertIn(
            "patch_text(charter, '', 'Include verified source links.')",
            json.loads(compiled["function"]["arguments"])["sql"],
        )

        call["function"]["arguments"] = json.dumps({
            "target_charter_text": "",
            "replacement_charter_text": "Research prospects. Include verified source links.",
        })
        compiled = ep._compile_charter_patch_tool_call(call, "Research prospects.")
        self.assertIn(
            "patch_text(charter, '', 'Include verified source links.')",
            json.loads(compiled["function"]["arguments"])["sql"],
        )

    def test_structured_charter_patch_rejects_invalid_values(self):
        def compile_params(params):
            return ep._compile_charter_patch_tool_call({
                "function": {"name": "sqlite_batch", "arguments": json.dumps(params)},
            })

        for params in (
            {},
            [],
            "old/new",
            {"target_charter_text": "Rule", "replacement_charter_text": ""},
            {"target_charter_text": "Rule", "replacement_charter_text": "Rule"},
            {"target_charter_text": "Rule\x00", "replacement_charter_text": "Better rule"},
            {"target_charter_text": 1, "replacement_charter_text": "Better rule"},
            {"preserve": "", "old": "Rule", "new": "Better rule"},
        ):
            with self.subTest(params=params):
                self.assertIsNone(compile_params(params))

    def _add_inbound_web_message(self, body):
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            is_primary=False,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_endpoint.address,
        )
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=user_endpoint,
            conversation=conversation,
            body=body,
        )

    def _add_outbound_web_message(self, conversation, body="Previous agent reply"):
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=build_web_agent_address(self.agent.id),
            is_primary=True,
        )
        return PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=agent_endpoint,
            conversation=conversation,
            body=body,
        )

    def _add_feedback_followup(
        self,
        feedback="You sound robotic.",
        *,
        initial_body="Initial request",
        prior_body="Previous agent reply",
    ):
        initial = self._add_inbound_web_message(initial_body)
        outbound = self._add_outbound_web_message(initial.conversation, prior_body)
        correction = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=initial.from_endpoint,
            conversation=initial.conversation,
            body=feedback,
        )
        return initial, outbound, correction

    def test_direct_correction_patch_requires_prior_outbound_message(self):
        self._add_inbound_web_message("You sound robotic.")

        self.assertIsNone(ep._direct_correction_context(self.agent))

    def test_direct_correction_patch_is_disabled_in_planning_mode(self):
        self._add_feedback_followup()
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        self.assertIsNone(ep._direct_correction_context(self.agent))

    def test_direct_correction_patch_is_required_for_followup_feedback(self):
        self._add_feedback_followup()

        self.assertIsNotNone(ep._direct_correction_context(self.agent))

    def test_quoted_feedback_across_a_newline_requires_a_patch(self):
        prior = "Curious what you think the biggest gap is right now."
        self._add_feedback_followup(f'"{prior}"\nthis is not so good.', prior_body=prior)

        self.assertIsNotNone(ep._direct_correction_context(self.agent))

    def test_batch_scoped_feedback_requires_only_a_transient_acknowledgement(self):
        self._add_feedback_followup("Only this batch, those long updates aren't useful.")

        self.assertTrue(ep._analyze_feedback_turn("Only this batch, those long updates aren't useful.").behavior)
        self.assertIsNone(ep._direct_correction_context(self.agent))

    def test_direct_correction_patch_requires_prior_outbound_in_same_conversation(self):
        initial = self._add_inbound_web_message("Initial request")
        self._add_outbound_web_message(initial.conversation)
        other_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=initial.from_endpoint.address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=initial.from_endpoint,
            conversation=other_conversation,
            body="You sound robotic.",
        )

        self.assertIsNone(ep._direct_correction_context(self.agent))

    def test_direct_correction_patch_requires_configure_authority(self):
        external_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="external@example.com",
        )
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address="agent@example.com",
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=external_endpoint.address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=True,
            from_endpoint=agent_endpoint,
            to_endpoint=external_endpoint,
            conversation=conversation,
            body="Previous agent reply",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=external_endpoint,
            conversation=conversation,
            body="You sound robotic.",
        )

        self.assertIsNone(ep._direct_correction_context(self.agent))

    def test_direct_correction_patch_uses_bound_inbound_scope(self):
        initial, _outbound, correction = self._add_feedback_followup()
        scope = capture_inbound_routing_scope(self.agent)
        token = bind_inbound_routing_scope(scope)
        self.addCleanup(reset_inbound_routing_scope, token)
        newer_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=initial.from_endpoint.address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=initial.from_endpoint,
            conversation=newer_conversation,
            body="What is the latest status?",
        )

        self.assertEqual(scope.message_id, correction.id)
        self.assertIsNotNone(ep._direct_correction_context(self.agent))

    def test_direct_correction_patch_uses_seq_to_break_timestamp_ties(self):
        _initial, outbound, correction = self._add_feedback_followup()
        shared_timestamp = timezone.now()
        PersistentAgentMessage.objects.filter(id=outbound.id).update(
            timestamp=shared_timestamp,
            seq="01AAAAAAAAAAAAAAAAAAAAAAAA",
        )
        PersistentAgentMessage.objects.filter(id=correction.id).update(
            timestamp=shared_timestamp,
            seq="01BBBBBBBBBBBBBBBBBBBBBBBB",
        )

        self.assertIsNotNone(ep._direct_correction_context(self.agent))

    def test_eval_mock_result_supports_url_rules(self):
        mock_config = {
            "http_request": {
                "rules": [
                    {
                        "url_contains": "geocoding-api.open-meteo.com",
                        "result": {"status": "ok", "content": {"results": []}},
                    }
                ],
                "default": {"status": "ok", "content": '{"current_weather": "72F, Sunny"}'},
            }
        }

        geocoding_result = ep._resolve_eval_mock_result(
            mock_config,
            "http_request",
            {"url": "https://geocoding-api.open-meteo.com/v1/search?name=Frederick"},
        )
        forecast_result = ep._resolve_eval_mock_result(
            mock_config,
            "http_request",
            {"url": "https://wttr.in/Frederick,MD?format=j1"},
        )

        self.assertEqual(geocoding_result["content"], {"results": []})
        self.assertIn("current_weather", forecast_result["content"])

    def test_eval_mock_result_supports_param_contains_rules(self):
        mock_config = {
            "mcp_brightdata_search_engine": {
                "rules": [
                    {
                        "param_contains": {"query": ["linkedin.com/jobs", "remote"]},
                        "result": {"status": "ok", "content": "linkedin result"},
                    }
                ],
                "default": {"status": "ok", "content": "default result"},
            }
        }

        matched_result = ep._resolve_eval_mock_result(
            mock_config,
            "mcp_brightdata_search_engine",
            {"query": 'remote "Full Stack Software Engineer" site:linkedin.com/jobs'},
        )
        default_result = ep._resolve_eval_mock_result(
            mock_config,
            "mcp_brightdata_search_engine",
            {"query": 'remote "Full Stack Software Engineer" site:indeed.com'},
        )

        self.assertEqual(matched_result["content"], "linkedin result")
        self.assertEqual(default_result["content"], "default result")

    def test_http_request_params_strip_linkified_url_artifacts(self):
        params = ep._normalize_tool_params(
            "http_request",
            {"url": 'https://releases.example.test/latest.json">https://releases.example.test/latest.json'},
        )

        self.assertEqual(params["url"], "https://releases.example.test/latest.json")

    def test_prepare_tool_batch_skips_duplicate_successful_http_request(self):
        trigger_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Cron trigger: 0 9 * * *",
        )
        PersistentAgentCronTrigger.objects.create(step=trigger_step, cron_expression="0 9 * * *")
        prior_step = PersistentAgentStep.objects.create(agent=self.agent, description="prior fetch")
        PersistentAgentToolCall.objects.create(
            step=prior_step,
            tool_name="http_request",
            tool_params={
                "method": "GET",
                "url": "https://api.example.test/daily.json",
                "will_continue_work": True,
            },
            result=json.dumps({"status": "ok", "status_code": 200, "content": "{}"}),
            status="complete",
        )

        with (
            patch.object(ep, "_enforce_tool_rate_limit", return_value=True) as mock_rate_limit,
            patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}) as mock_credit,
        ):
            prepared = ep._prepare_tool_batch(
                self.agent,
                tool_calls=[
                    {
                        "id": "call_duplicate",
                        "function": {
                            "name": "http_request",
                            "arguments": json.dumps(
                                {
                                    "method": "GET",
                                    "url": "https://api.example.test/daily.json",
                                    "will_continue_work": False,
                                }
                            ),
                        },
                    }
                ],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=False,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        mock_rate_limit.assert_not_called()
        mock_credit.assert_not_called()
        self.assertEqual(PersistentAgentToolCall.objects.filter(tool_name="http_request").count(), 1)
        duplicate_step = PersistentAgentStep.objects.get(
            agent=self.agent,
            description__startswith="Skipped duplicate http_request",
        )
        self.assertIn("send the final message next", duplicate_step.description)

    def test_prepare_tool_batch_keeps_http_request_when_prior_result_failed(self):
        trigger_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Cron trigger: 0 9 * * *",
        )
        PersistentAgentCronTrigger.objects.create(step=trigger_step, cron_expression="0 9 * * *")
        prior_step = PersistentAgentStep.objects.create(agent=self.agent, description="prior failed fetch")
        PersistentAgentToolCall.objects.create(
            step=prior_step,
            tool_name="http_request",
            tool_params={"method": "GET", "url": "https://api.example.test/daily.json"},
            result=json.dumps({"status": "error", "message": "temporary failure"}),
            status="error",
        )

        with (
            patch.object(ep, "_enforce_tool_rate_limit", return_value=True) as mock_rate_limit,
            patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}) as mock_credit,
        ):
            prepared = ep._prepare_tool_batch(
                self.agent,
                tool_calls=[
                    {
                        "id": "call_retry",
                        "function": {
                            "name": "http_request",
                            "arguments": json.dumps(
                                {"method": "GET", "url": "https://api.example.test/daily.json"}
                            ),
                        },
                    }
                ],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=False,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual(len(prepared.prepared_calls), 1)
        self.assertFalse(prepared.followup_required)
        mock_rate_limit.assert_called_once()
        mock_credit.assert_called_once()

    def test_invalid_custom_tool_json_correction_preserves_same_tool_retry(self):
        with (
            patch.object(ep, "_enforce_tool_rate_limit", return_value=True) as mock_rate_limit,
            patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}) as mock_credit,
        ):
            prepared = ep._prepare_tool_batch(
                self.agent,
                tool_calls=[
                    {
                        "id": "call_bad_json",
                        "function": {
                            "name": "create_custom_tool",
                            "arguments": '{"name": "fetch_events", "source_code": "unterminated',
                        },
                    }
                ],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=False,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        mock_rate_limit.assert_not_called()
        mock_credit.assert_not_called()
        correction_step = PersistentAgentStep.objects.get(
            agent=self.agent,
            description__startswith="Tool call error: arguments for create_custom_tool were not valid JSON",
        )
        self.assertIn("do not switch tools", correction_step.description)
        self.assertIn("retry create_custom_tool with source_code instead of using create_file", correction_step.description)

    def test_skipped_progress_chat_keeps_batch_followup_required(self):
        skipped_chat = ep._ToolExecutionOutcome(
            prepared=ep._PreparedToolExecution(
                idx=1,
                tool_name="send_chat_message",
                tool_params={
                    "body": "I've got the details. Let me deliver the structured results now.",
                    "will_continue_work": True,
                },
                exec_params={},
                pending_step=None,
                credits_consumed=None,
                consumed_credit=None,
                call_id="call_chat",
                explicit_continue=True,
                inferred_continue=False,
                parallel_safe=False,
                parallel_ineligible_reason=None,
            ),
            result={
                "status": "ok",
                "message": "Skipped routine progress-only chat message.",
                "auto_sleep_ok": False,
                "skipped": True,
            },
            duration_ms=1,
            updated_tools=None,
            variable_map={},
        )
        sqlite_stop = ep._ToolExecutionOutcome(
            prepared=ep._PreparedToolExecution(
                idx=2,
                tool_name="sqlite_batch",
                tool_params={"sql": "UPDATE charter SET content = content", "will_continue_work": False},
                exec_params={},
                pending_step=None,
                credits_consumed=None,
                consumed_credit=None,
                call_id="call_sql",
                explicit_continue=False,
                inferred_continue=False,
                parallel_safe=False,
                parallel_ineligible_reason=None,
            ),
            result={"status": "ok", "auto_sleep_ok": True},
            duration_ms=1,
            updated_tools=None,
            variable_map={},
        )

        finalized = ep._finalize_tool_batch(
            self.agent,
            [skipped_chat, sqlite_stop],
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertTrue(finalized.followup_required)
        self.assertFalse(finalized.message_delivery_ok)
        self.assertIs(finalized.last_explicit_continue, False)

    def test_send_chat_skips_got_result_progress_before_actual_report(self):
        start_web_session(self.agent, self.user)

        result = execute_send_chat_message(
            self.agent,
            {
                "body": (
                    "Got the result! The browser task found the Washington DC pollution data. "
                    "Let me report it and set up the regular monitoring."
                ),
                "will_continue_work": True,
            },
        )

        self.assertEqual(result.get("status"), "ok")
        self.assertTrue(result.get("skipped"))
        self.assertIn("deliver the substantive reply in this web chat", result.get("message", ""))
        self.assertIn("do not switch to email or SMS", result.get("message", ""))
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=True).count(),
            0,
        )

    def test_send_chat_skips_returned_data_progress_even_when_marked_stop(self):
        start_web_session(self.agent, self.user)

        skipped = execute_send_chat_message(
            self.agent,
            {
                "body": (
                    "Great news \u2014 the SimWeather site returned the data! "
                    "Let me update my charter and schedule, and report back."
                ),
                "will_continue_work": False,
            },
        )

        self.assertEqual(skipped.get("status"), "ok")
        self.assertTrue(skipped.get("skipped"))
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=True).count(),
            0,
        )

        delivered = execute_send_chat_message(
            self.agent,
            {
                "body": "Washington DC pollution index: Moderate (55).",
                "will_continue_work": False,
            },
        )

        self.assertEqual(delivered.get("status"), "ok")
        self.assertFalse(delivered.get("skipped", False))
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=True).count(),
            1,
        )

    def test_send_chat_skips_sqlite_autocorrection_recovery_progress(self):
        start_web_session(self.agent, self.user)

        skipped = execute_send_chat_message(
            self.agent,
            {
                "body": (
                    "The auto-correction was wrong - the data sits at root level, not under `$.content`. "
                    "I need to drop and recreate `plan_candidates` with the correct paths."
                ),
                "will_continue_work": True,
            },
        )

        self.assertEqual(skipped.get("status"), "ok")
        self.assertTrue(skipped.get("skipped"))
        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=True).count(),
            0,
        )

    def test_send_chat_skips_endpoint_and_search_kickoff_progress(self):
        start_web_session(self.agent, self.user)

        for body in (
            "All 4 JSON endpoints are fetched. Now I'll query the working table.",
            "Hey! I'm Eval Agent. Let's dig up three current remote job listings from different sources right now.",
        ):
            result = execute_send_chat_message(
                self.agent,
                {
                    "body": body,
                    "will_continue_work": True,
                },
            )

            self.assertEqual(result.get("status"), "ok")
            self.assertTrue(result.get("skipped"))

        self.assertEqual(
            PersistentAgentMessage.objects.filter(owner_agent=self.agent, is_outbound=True).count(),
            0,
        )

    def test_planning_chat_question_stays_chat_without_auto_conversion(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])

        prepared = ep._prepare_tool_batch(
            self.agent,
            tool_calls=[
                {
                    "id": "call_chat",
                    "function": {
                        "name": "send_chat_message",
                        "arguments": json.dumps(
                            {
                                "body": (
                                    "One quick thing though - I need to know what industry or space "
                                    "you're looking at. Got a target company, product category, or industry for me?"
                                ),
                                "will_continue_work": False,
                            }
                        ),
                    },
                },
            ],
            budget_ctx=None,
            eval_run_id=None,
            heartbeat=None,
            lock_extender=None,
            credit_snapshot={},
            allow_inferred_message_continue=True,
            has_non_sleep_calls=True,
            has_user_facing_message=True,
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertEqual(len(prepared.prepared_calls), 1)
        routed = prepared.prepared_calls[0]
        self.assertEqual(routed.tool_name, "send_chat_message")
        self.assertIn("target company", routed.tool_params["body"])
        self.assertFalse(routed.tool_params["will_continue_work"])

    def test_clarify_chat_question_stays_chat_outside_planning(self):
        prepared = ep._prepare_tool_batch(
            self.agent,
            tool_calls=[
                {
                    "id": "call_chat",
                    "function": {
                        "name": "send_chat_message",
                        "arguments": json.dumps(
                            {
                                "body": (
                                    "I don't have the client's email address.\n\n"
                                    "Could you clarify:\n\n"
                                    "1. What project are you referring to?\n"
                                    "2. Who is the client and email address?"
                                ),
                                "will_continue_work": False,
                            }
                        ),
                    },
                },
            ],
            budget_ctx=None,
            eval_run_id=None,
            heartbeat=None,
            lock_extender=None,
            credit_snapshot={},
            allow_inferred_message_continue=True,
            has_non_sleep_calls=True,
            has_user_facing_message=True,
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertEqual(len(prepared.prepared_calls), 1)
        routed = prepared.prepared_calls[0]
        self.assertEqual(routed.tool_name, "send_chat_message")
        self.assertIn("project", routed.tool_params["body"])
        self.assertFalse(routed.tool_params["will_continue_work"])

    def test_defaultable_schedule_setup_question_gets_runtime_correction(self):
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            is_primary=False,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_endpoint.address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=user_endpoint,
            conversation=conversation,
            body="Set a daily 9am ET schedule for a competitor pricing digest.",
        )

        prepared = ep._prepare_tool_batch(
            self.agent,
            tool_calls=[
                {
                    "id": "call_chat",
                    "function": {
                        "name": "send_chat_message",
                        "arguments": json.dumps(
                            {
                                "body": (
                                    "Got it. I just need a few details to make the digest useful. "
                                    "Let me ask before I configure anything."
                                ),
                                "will_continue_work": True,
                            }
                        ),
                    },
                },
                {
                    "id": "call_question",
                    "function": {
                        "name": "request_human_input",
                        "arguments": json.dumps(
                            {
                                "question": "Which competitors/products should I track, and where should I send it?",
                                "will_continue_work": False,
                            }
                        ),
                    },
                },
            ],
            budget_ctx=None,
            eval_run_id=None,
            heartbeat=None,
            lock_extender=None,
            credit_snapshot={},
            allow_inferred_message_continue=True,
            has_non_sleep_calls=True,
            has_user_facing_message=True,
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__contains="reversible recurring setup request",
            ).exists()
        )

    def test_defaultable_schedule_setup_request_items_get_runtime_correction(self):
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            is_primary=False,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_endpoint.address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=user_endpoint,
            conversation=conversation,
            body="Set a daily 9am ET schedule for a competitor pricing digest.",
        )

        prepared = ep._prepare_tool_batch(
            self.agent,
            tool_calls=[
                {
                    "id": "call_question",
                    "function": {
                        "name": "request_human_input",
                        "arguments": json.dumps(
                            {
                                "question": "I need a few specifics to set up your daily competitor pricing digest:",
                                "requests": [
                                    {"question": "Which competitors should I track?"},
                                    {"question": "Where should I pull their pricing from?"},
                                ],
                                "will_continue_work": True,
                            }
                        ),
                    },
                },
            ],
            budget_ctx=None,
            eval_run_id=None,
            heartbeat=None,
            lock_extender=None,
            credit_snapshot={},
            allow_inferred_message_continue=True,
            has_non_sleep_calls=True,
            has_user_facing_message=False,
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__contains="reversible recurring setup request",
            ).exists()
        )

    def test_request_human_input_question_texts_reads_questions_alias(self):
        texts = ep._request_human_input_question_texts(
            {
                "question": "Top-level question?",
                "questions": [
                    {"question": "First nested question?"},
                    {"question": "Second nested question?"},
                ],
            }
        )

        self.assertEqual(
            texts,
            [
                "Top-level question?",
                "First nested question?",
                "Second nested question?",
            ],
        )

    def test_defaultable_setup_detector_handles_detail_survey_phrasing(self):
        self.assertTrue(
            ep._looks_like_defaultable_setup_question(
                "What details do you have? Specifically, I need to know:"
            )
        )

    def test_defaultable_setup_guard_allows_planning_questions_in_planning_mode(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            is_primary=False,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_endpoint.address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=user_endpoint,
            conversation=conversation,
            body="Help me plan a competitor monitoring setup.",
        )

        prepared = ep._prepare_tool_batch(
            self.agent,
            tool_calls=[
                {
                    "id": "call_question",
                    "function": {
                        "name": "request_human_input",
                        "arguments": json.dumps(
                            {
                                "requests": [
                                    {
                                        "question": "Which competitors should I track?",
                                        "options": [
                                            {
                                                "title": "Top three",
                                                "description": "Track the most important competitors.",
                                            }
                                        ],
                                    }
                                ],
                                "will_continue_work": True,
                            }
                        ),
                    },
                },
            ],
            budget_ctx=None,
            eval_run_id=None,
            heartbeat=None,
            lock_extender=None,
            credit_snapshot={},
            allow_inferred_message_continue=True,
            has_non_sleep_calls=True,
            has_user_facing_message=False,
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertFalse(prepared.followup_required)
        self.assertEqual(len(prepared.prepared_calls), 1)
        self.assertFalse(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__contains="reversible recurring setup request",
            ).exists()
        )

    def test_defaultable_charter_detail_question_gets_runtime_correction(self):
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            is_primary=False,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_endpoint.address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=user_endpoint,
            conversation=conversation,
            body=(
                "Going forward, make the vendor risk monitor specific: track security incidents, "
                "pricing changes, SLA outages, and contract renewal dates. Include source links in each update."
            ),
        )

        prepared = ep._prepare_tool_batch(
            self.agent,
            tool_calls=[
                {
                    "id": "call_question",
                    "function": {
                        "name": "request_human_input",
                        "arguments": json.dumps(
                            {
                                "question": (
                                    "Which vendors should I track, and how often should I check for updates?"
                                ),
                                "will_continue_work": False,
                            }
                        ),
                    },
                }
            ],
            budget_ctx=None,
            eval_run_id=None,
            heartbeat=None,
            lock_extender=None,
            credit_snapshot={},
            allow_inferred_message_continue=True,
            has_non_sleep_calls=True,
            has_user_facing_message=True,
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__contains="reversible recurring setup request",
            ).exists()
        )

    def test_user_requested_monitoring_scope_question_is_allowed(self):
        self.assertFalse(
            ep._looks_like_defaultable_recurring_setup_request(
                "Ask which competitors and update types matter before setting up monitoring."
            )
        )

    def test_delivered_progress_chat_marks_pending_reply(self):
        progress_chat = ep._ToolExecutionOutcome(
            prepared=ep._PreparedToolExecution(
                idx=1,
                tool_name="send_chat_message",
                tool_params={
                    "body": "Let me pull those details now.",
                    "will_continue_work": True,
                },
                exec_params={},
                pending_step=None,
                credits_consumed=None,
                consumed_credit=None,
                call_id="call_chat",
                explicit_continue=True,
                inferred_continue=False,
                parallel_safe=False,
                parallel_ineligible_reason=None,
            ),
            result={
                "status": "ok",
                "message": "Web chat message sent.",
                "auto_sleep_ok": False,
            },
            duration_ms=1,
            updated_tools=None,
            variable_map={},
        )

        finalized = ep._finalize_tool_batch(
            self.agent,
            [progress_chat],
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertTrue(finalized.message_delivery_ok)
        self.assertTrue(finalized.progress_message_delivery_ok)
        self.assertFalse(finalized.terminal_message_delivery_ok)
        self.assertFalse(finalized.followup_required)
        self.assertIs(finalized.last_explicit_continue, True)

    def test_terminal_chat_marked_continue_preserves_explicit_continue(self):
        final_chat = ep._ToolExecutionOutcome(
            prepared=ep._PreparedToolExecution(
                idx=1,
                tool_name="send_chat_message",
                tool_params={
                    "body": (
                        "## Bitcoin Price\n\n"
                        "Bitcoin is trading at $68,500.\n\n"
                        "Sources:\n"
                        "- https://example.test/price\n"
                    ),
                    "will_continue_work": True,
                },
                exec_params={},
                pending_step=None,
                credits_consumed=None,
                consumed_credit=None,
                call_id="call_chat",
                explicit_continue=True,
                inferred_continue=False,
                parallel_safe=False,
                parallel_ineligible_reason=None,
            ),
            result={
                "status": "ok",
                "message": "Web chat message sent.",
                "auto_sleep_ok": False,
            },
            duration_ms=1,
            updated_tools=None,
            variable_map={},
        )

        finalized = ep._finalize_tool_batch(
            self.agent,
            [final_chat],
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertTrue(finalized.message_delivery_ok)
        self.assertFalse(finalized.terminal_message_delivery_ok)
        self.assertTrue(finalized.progress_message_delivery_ok)
        self.assertFalse(finalized.followup_required)
        self.assertIs(finalized.last_explicit_continue, True)

    def test_terminal_planning_answer_skips_stale_planning_mode(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        final_chat = ep._ToolExecutionOutcome(
            prepared=ep._PreparedToolExecution(
                idx=1,
                tool_name="send_chat_message",
                tool_params={
                    "body": (
                        "MikroTik has several new networking products worth watching.\n\n"
                        "Sources:\n"
                        "- https://example.test/mikrotik\n"
                    ),
                    "will_continue_work": False,
                },
                exec_params={},
                pending_step=None,
                credits_consumed=None,
                consumed_credit=None,
                call_id="call_chat",
                explicit_continue=False,
                inferred_continue=False,
                parallel_safe=False,
                parallel_ineligible_reason=None,
            ),
            result={
                "status": "ok",
                "message": "Web chat message sent.",
                "auto_sleep_ok": True,
            },
            duration_ms=1,
            updated_tools=None,
            variable_map={},
        )
        finalized = ep._finalize_tool_batch(
            self.agent,
            [final_chat],
            attach_completion=lambda step_kwargs: None,
            attach_prompt_archive=lambda step: None,
        )

        self.assertTrue(finalized.terminal_message_delivery_ok)
        self.assertTrue(
            ep._should_skip_stale_planning_mode_after_terminal_delivery(
                self.agent,
                finalized,
                followup_required=finalized.followup_required,
            )
        )
        with patch("console.agent_chat.signals.emit_agent_planning_state_update") as mock_emit:
            self.assertTrue(ep._skip_stale_planning_mode_after_terminal_delivery(self.agent))

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.SKIPPED)
        mock_emit.assert_called_once()

    def test_terminal_planning_answer_waits_when_followup_required(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        finalized = ep._FinalizedToolBatch(
            executed_calls=1,
            followup_required=False,
            message_delivery_ok=True,
            last_explicit_continue=False,
            inferred_message_continue_this_iteration=False,
            executed_non_message_action=False,
            terminal_message_delivery_ok=True,
        )

        self.assertFalse(
            ep._should_skip_stale_planning_mode_after_terminal_delivery(
                self.agent,
                finalized,
                followup_required=True,
            )
        )
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.planning_state, PersistentAgent.PlanningState.PLANNING)

    def _prepare_tool_batch_for_test(self, tool_calls, *, has_user_facing_message=False):
        with (
            patch.object(ep, "_enforce_tool_rate_limit", return_value=True),
            patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}),
        ):
            return ep._prepare_tool_batch(
                self.agent,
                tool_calls=tool_calls,
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True, has_non_sleep_calls=True,
                has_user_facing_message=has_user_facing_message,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

    @staticmethod
    def _raw_tool_call(tool_name, params, call_id=None):
        return {
            "id": call_id or f"call_{tool_name}",
            "function": {"name": tool_name, "arguments": json.dumps(params)},
        }

    def test_prepare_tool_batch_skips_one_off_config_churn_before_terminal_message(self):
        self._add_inbound_web_message("What's the latest Bitcoin price right now?")
        prepared = self._prepare_tool_batch_for_test(
            [
                self._raw_tool_call("sqlite_batch", {
                    "sql": "UPDATE __agent_config SET charter='Track Bitcoin price after every answer' WHERE id=1;",
                    "will_continue_work": True,
                }, "call_config"),
                self._raw_tool_call("send_chat_message", {
                    "body": "Bitcoin is trading at $68,500.\n\nSources:\n- https://example.test/price",
                    "will_continue_work": False,
                }, "call_chat"),
            ],
            has_user_facing_message=True,
        )

        self.assertEqual([call.tool_name for call in prepared.prepared_calls], ["send_chat_message"])
        self.assertFalse(prepared.followup_required)
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__startswith="Skipped unrelated __agent_config mutation",
            ).exists()
        )

    def test_one_off_config_guard_allows_emotion_only_mutation(self):
        self._add_inbound_web_message("What's the latest Bitcoin price right now?")

        self.assertFalse(
            ep._should_skip_irrelevant_agent_config_mutation(
                self.agent,
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "UPDATE __agent_config SET emotion='🙂', "
                        "emotion_timeout_seconds=3600 WHERE id=1"
                    ),
                },
                batch_has_terminal_message=True,
            )
        )
        self.assertTrue(
            ep._should_skip_irrelevant_agent_config_mutation(
                self.agent,
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "UPDATE __agent_config SET charter='Track every price lookup', "
                        "emotion='🙂', emotion_timeout_seconds=3600 WHERE id=1"
                    ),
                },
                batch_has_terminal_message=True,
            )
        )

    def test_prepare_tool_batch_skips_batch_scoped_charter_mutation_without_reply(self):
        self._add_inbound_web_message("Only this batch, these long updates aren't useful.")

        prepared = self._prepare_tool_batch_for_test(
            [self._raw_tool_call("sqlite_batch", {
                "sql": "UPDATE __agent_config SET charter='Keep every update short' WHERE id=1;",
                "will_continue_work": False,
            }, "call_config")]
        )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)

    def test_temporary_charter_mutation_blocks_mixed_domain_sql(self):
        self._add_inbound_web_message("For this batch, keep the notes short, then analyze the CRM rows.")

        self.assertTrue(
            ep._should_skip_irrelevant_agent_config_mutation(
                self.agent,
                tool_name="sqlite_batch",
                tool_params={
                    "sql": (
                        "UPDATE __agent_config SET charter='Keep notes short' WHERE id=1;"
                        "CREATE TABLE crm_rows(id TEXT PRIMARY KEY);"
                    ),
                },
                batch_has_terminal_message=False,
            )
        )

    def test_transient_wording_does_not_block_explicit_schedule_pause(self):
        self._add_inbound_web_message("For now, pause your schedule.")

        self.assertFalse(ep._should_skip_irrelevant_agent_config_mutation(
            self.agent,
            tool_name="sqlite_batch",
            tool_params={"sql": "UPDATE __agent_config SET schedule=NULL WHERE id=1"},
            batch_has_terminal_message=False,
        ))

    def test_temporary_report_preference_does_not_block_recurring_setup(self):
        self._add_inbound_web_message("For this report, keep it brief. Set up a daily customer digest.")

        self.assertFalse(ep._should_skip_irrelevant_agent_config_mutation(
            self.agent,
            tool_name="sqlite_batch",
            tool_params={
                "sql": (
                    "UPDATE __agent_config SET "
                    "charter='Send a daily customer digest', schedule='0 9 * * *' WHERE id=1"
                ),
            },
            batch_has_terminal_message=False,
        ))

    def test_prepare_tool_batch_keeps_durable_charter_update_with_terminal_message(self):
        self._add_inbound_web_message(
            "Going forward, always include source links in price reports. "
            "Now tell me the latest Bitcoin price."
        )

        prepared = self._prepare_tool_batch_for_test(
            [
                self._raw_tool_call("sqlite_batch", {
                    "sql": "UPDATE __agent_config SET charter='Answer price reports with source links' WHERE id=1;",
                    "will_continue_work": True,
                }, "call_config"),
                self._raw_tool_call("send_chat_message", {
                    "body": "Bitcoin is trading at $68,500.\n\nSources:\n- https://example.test/price",
                    "will_continue_work": False,
                }, "call_chat"),
            ],
            has_user_facing_message=True,
        )

        self.assertEqual(
            [call.tool_name for call in prepared.prepared_calls],
            ["sqlite_batch", "send_chat_message"],
        )

    def test_config_intent_detector_keeps_setup_scope_process_and_customer_changes_durable(self):
        durable_examples = [
            "Set up a daily competitor pricing digest for Fridays.",
            "Change my scope to enterprise customers only going forward.",
            "Remember this customer context for renewal alerts.",
            "Update your process: verify source links before reporting.",
            "Can we just make that a rule?",
            "Do I have to request that you add links each time?",
            "I shouldnt have to ask for source links in every report.",
            "Feedback: I prefer comparison tables for reports.",
        ]

        for text in durable_examples:
            with self.subTest(text=text):
                self.assertTrue(ep._user_text_has_durable_config_intent(text))
                self.assertFalse(ep._looks_like_one_off_user_task(text))

        one_off_feedback_tasks = [
            "Summarize customer feedback from Slack today.",
            "Give me feedback on this memo.",
            "Any feedback on this draft?",
            "Thanks for the feedback.",
        ]

        for text in one_off_feedback_tasks:
            with self.subTest(text=text):
                self.assertFalse(ep._user_text_has_durable_config_intent(text))

        self.assertTrue(
            ep._looks_like_one_off_user_task("Summarize customer feedback from Slack today.")
        )
        self.assertFalse(ep._user_text_has_durable_config_intent("For this answer, prefer bullets."))
        self.assertTrue(ep._looks_like_one_off_user_task("Tell me the latest funding news for Acme."))

    def test_planning_execute_now_search_tools_first_gets_runtime_correction(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        self._add_inbound_web_message(
            "Do not ask questions. Just execute now: research five competitors and email me the findings."
        )

        with (
            patch.object(ep, "_enforce_tool_rate_limit", return_value=True) as mock_rate_limit,
            patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}) as mock_credit,
        ):
            prepared = ep._prepare_tool_batch(
                self.agent,
                tool_calls=[
                    {
                        "id": "call_search",
                        "function": {
                            "name": "search_tools",
                            "arguments": json.dumps({"query": "competitor research and email tools"}),
                        },
                    },
                ],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=False,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        mock_rate_limit.assert_not_called()
        mock_credit.assert_not_called()
        self.assertEqual(PersistentAgentToolCall.objects.filter(tool_name="search_tools").count(), 0)
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__startswith="Skipped search_tools before planning was completed",
            ).exists()
        )

    def test_planning_research_can_still_use_search_tools(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        self._add_inbound_web_message(
            "Help me plan competitor monitoring; research likely data sources before asking final scope questions."
        )

        with (
            patch.object(ep, "_enforce_tool_rate_limit", return_value=True) as mock_rate_limit,
            patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}) as mock_credit,
        ):
            prepared = ep._prepare_tool_batch(
                self.agent,
                tool_calls=[
                    {
                        "id": "call_search",
                        "function": {
                            "name": "search_tools",
                            "arguments": json.dumps({"query": "competitor monitoring data sources"}),
                        },
                    },
                ],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=False,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual([call.tool_name for call in prepared.prepared_calls], ["search_tools"])
        self.assertFalse(prepared.followup_required)
        mock_rate_limit.assert_called_once()
        mock_credit.assert_called_once()

    def test_planning_ready_chat_without_end_planning_gets_runtime_correction(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        self._add_inbound_web_message(
            "Set up a daily 9 AM ET SEC enforcement RSS digest in web chat."
        )

        with (
            patch.object(ep, "_enforce_tool_rate_limit", return_value=True) as mock_rate_limit,
            patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}) as mock_credit,
        ):
            prepared = ep._prepare_tool_batch(
                self.agent,
                tool_calls=[
                    {
                        "id": "call_chat",
                        "function": {
                            "name": "send_chat_message",
                            "arguments": json.dumps(
                                {
                                    "body": "The plan's clear. Let's lock it in and get this rolling.",
                                    "will_continue_work": True,
                                }
                            ),
                        },
                    },
                ],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=True,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual(prepared.prepared_calls, [])
        self.assertTrue(prepared.followup_required)
        mock_rate_limit.assert_not_called()
        mock_credit.assert_not_called()
        self.assertTrue(
            PersistentAgentStep.objects.filter(
                agent=self.agent,
                description__startswith="Planning Mode is active and the plan appears clear",
            ).exists()
        )

    def test_planning_ready_chat_with_end_planning_is_allowed(self):
        self.agent.planning_state = PersistentAgent.PlanningState.PLANNING
        self.agent.save(update_fields=["planning_state", "updated_at"])
        self._add_inbound_web_message(
            "Set up a daily 9 AM ET SEC enforcement RSS digest in web chat."
        )

        with (
            patch.object(ep, "_enforce_tool_rate_limit", return_value=True),
            patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}),
        ):
            prepared = ep._prepare_tool_batch(
                self.agent,
                tool_calls=[
                    {
                        "id": "call_chat",
                        "function": {
                            "name": "send_chat_message",
                            "arguments": json.dumps(
                                {
                                    "body": "The plan's clear. Let's lock it in and get this rolling.",
                                    "will_continue_work": True,
                                }
                            ),
                        },
                    },
                    {
                        "id": "call_end",
                        "function": {
                            "name": "end_planning",
                            "arguments": json.dumps({"full_plan": "Send a daily SEC enforcement RSS digest."}),
                        },
                    },
                ],
                budget_ctx=None,
                eval_run_id=None,
                heartbeat=None,
                lock_extender=None,
                credit_snapshot={},
                allow_inferred_message_continue=True,
                has_non_sleep_calls=True,
                has_user_facing_message=True,
                attach_completion=lambda step_kwargs: None,
                attach_prompt_archive=lambda step: None,
            )

        self.assertEqual([call.tool_name for call in prepared.prepared_calls], ["send_chat_message", "end_planning"])
        self.assertFalse(prepared.followup_required)

    def _mock_completion(self, content, *, reasoning_content=None):
        msg = MagicMock()
        msg.tool_calls = None
        msg.function_call = None
        msg.content = content
        if reasoning_content is not None:
            msg.reasoning_content = reasoning_content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_requires_active_web_session_for_last_chat(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        prior_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Tool call: send_chat_message",
        )
        PersistentAgentToolCall.objects.create(
            step=prior_step,
            tool_name="send_chat_message",
            tool_params={
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "old",
                "will_continue_work": True,
            },
            result="{}",
        )

        resp = self._mock_completion("New implied web chat")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._schedule_agent_follow_up",
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNotNone(correction_step)
        self.assertIn(
            "requester's inbound channel",
            correction_step.description,
        )

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_requires_active_web_session_for_inbound_web_message(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        user_address = build_web_user_address(self.user.id, self.agent.id)
        agent_address = build_web_agent_address(self.agent.id)

        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=agent_address,
            is_primary=True,
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.WEB,
            address=user_address,
            is_primary=False,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.WEB,
            address=user_address,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=user_endpoint,
            conversation=conversation,
            body="Inbound web message",
        )

        resp = self._mock_completion("New implied web chat")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._schedule_agent_follow_up",
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNotNone(correction_step)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_requires_active_web_session_with_preferred_endpoint(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        resp = self._mock_completion("Hello via implied web chat")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNotNone(correction_step)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_allows_eval_web_preferred_endpoint_without_active_session(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            owner_agent=None,
        )
        self.agent.execution_environment = "eval"
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["execution_environment", "preferred_contact_endpoint"])

        resp = self._mock_completion("Here are the bundled results.")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._schedule_agent_follow_up",
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertEqual(params["to_address"], endpoint.address)
        self.assertEqual(params["body"], "Here are the bundled results.")

    def test_implied_send_prefers_deliverable_web_session(self):
        start_web_session(self.agent, self.user)

        context = _get_implied_send_context(self.agent)

        self.assertIsNotNone(context)
        self.assertEqual(context["channel"], "web")
        self.assertEqual(
            context["to_address"],
            build_web_user_address(self.user.id, self.agent.id),
        )

    def test_latest_requester_beats_other_web_presence_and_preference(self):
        requester_message = self._add_inbound_web_message("Can you answer me here?")
        start_web_session(self.agent, self.user)

        observer = get_user_model().objects.create_user(
            username="implied-observer@example.com",
            email="implied-observer@example.com",
            password="password",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=observer)
        observer_address = build_web_user_address(observer.id, self.agent.id)
        observer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=observer_address,
        )
        self.agent.preferred_contact_endpoint = observer_endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])
        start_web_session(self.agent, observer)

        context = _get_implied_send_context(self.agent)
        self.assertEqual(context["to_address"], requester_message.from_endpoint.address)

        result = execute_send_chat_message(self.agent, {"body": "Here is your answer."})

        self.assertEqual(result["status"], "ok")
        outbound = PersistentAgentMessage.objects.get(
            owner_agent=self.agent,
            is_outbound=True,
            body="Here is your answer.",
        )
        self.assertEqual(outbound.to_endpoint.address, requester_message.from_endpoint.address)

    def test_default_chat_does_not_switch_latest_email_request_to_web(self):
        start_web_session(self.agent, self.user)
        email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=email_endpoint,
            conversation=email_conversation,
            body="Please answer this email.",
        )

        self.assertIsNone(_get_implied_send_context(self.agent))
        result = execute_send_chat_message(self.agent, {"body": "Wrong channel"})

        self.assertEqual(result["status"], "error")
        self.assertIn("inbound channel", result["message"])
        self.assertIn("do not provide another web target", result["message"])
        self.assertIs(result["retryable"], False)
        self.assertFalse(PersistentAgentMessage.objects.filter(is_outbound=True).exists())

    def test_explicit_web_delivery_remains_available_from_email_context(self):
        start_web_session(self.agent, self.user)
        email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=email_endpoint,
            conversation=email_conversation,
            body="Send Maya this update in web chat.",
        )

        result = execute_send_chat_message(
            self.agent,
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "The requested update.",
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(PersistentAgentMessage.objects.filter(body="The requested update.").exists())

    def test_inbound_scope_uses_run_boundary_and_survives_later_trigger(self):
        requester_message = self._add_inbound_web_message("Can you answer me here?")
        start_web_session(self.agent, self.user)
        observer = get_user_model().objects.create_user(
            username="routing-observer@example.com",
            email="routing-observer@example.com",
            password="password",
        )
        AgentCollaborator.objects.create(agent=self.agent, user=observer)
        observer_address = build_web_user_address(observer.id, self.agent.id)
        observer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=observer_address,
        )
        self.agent.preferred_contact_endpoint = observer_endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])
        start_web_session(self.agent, observer)

        process_step = PersistentAgentStep.objects.create(agent=self.agent, description="Process events")
        cron_step = PersistentAgentStep.objects.create(agent=self.agent, description="Cron run")
        PersistentAgentCronTrigger.objects.create(step=cron_step, cron_expression="0 9 * * *")
        PersistentAgentStep.objects.filter(pk=cron_step.pk).update(
            created_at=requester_message.timestamp + timedelta(seconds=1),
        )

        pending_scope = capture_inbound_routing_scope(self.agent, pending_inbound=True)
        self.assertEqual(pending_scope.message_id, requester_message.id)
        scope = capture_inbound_routing_scope(
            self.agent,
            pending_inbound=False,
            background_before=process_step.created_at,
        )
        token = bind_inbound_routing_scope(scope)
        try:
            later_trigger = PersistentAgentStep.objects.create(agent=self.agent, description="Later proactive run")
            PersistentAgentSystemStep.objects.create(
                step=later_trigger,
                code=PersistentAgentSystemStep.Code.PROACTIVE_TRIGGER,
            )

            context = _get_implied_send_context(self.agent)
            stream_target = resolve_web_stream_target(self.agent)
            result = execute_send_chat_message(self.agent, {"body": "Here is your answer."})
        finally:
            reset_inbound_routing_scope(token)

        self.assertEqual(context["to_address"], requester_message.from_endpoint.address)
        self.assertEqual(stream_target.address, requester_message.from_endpoint.address)
        self.assertEqual(result["status"], "ok")
        outbound = PersistentAgentMessage.objects.get(body="Here is your answer.")
        self.assertEqual(outbound.to_endpoint.address, requester_message.from_endpoint.address)

    def test_proactive_run_does_not_treat_historical_email_as_current_requester(self):
        start_web_session(self.agent, self.user)
        email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        inbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=email_endpoint,
            conversation=email_conversation,
            body="An older email request.",
        )
        trigger_step = PersistentAgentStep.objects.create(agent=self.agent, description="Scheduled run")
        PersistentAgentSystemStep.objects.create(
            step=trigger_step,
            code=PersistentAgentSystemStep.Code.PROACTIVE_TRIGGER,
        )
        PersistentAgentStep.objects.filter(pk=trigger_step.pk).update(
            created_at=inbound.timestamp + timedelta(seconds=1),
        )

        context = _get_implied_send_context(self.agent)
        self.assertEqual(context["to_address"], build_web_user_address(self.user.id, self.agent.id))

        result = execute_send_chat_message(self.agent, {"body": "Scheduled report"})
        self.assertEqual(result["status"], "ok")
        outbound = PersistentAgentMessage.objects.get(body="Scheduled report")
        self.assertEqual(outbound.conversation.channel, CommsChannel.WEB)

    def test_cron_run_does_not_treat_historical_email_as_current_requester(self):
        start_web_session(self.agent, self.user)
        email_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.EMAIL,
            address=self.user.email,
        )
        inbound = PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=email_endpoint,
            conversation=email_conversation,
            body="An older email request.",
        )
        trigger_step = PersistentAgentStep.objects.create(agent=self.agent, description="Cron run")
        PersistentAgentCronTrigger.objects.create(step=trigger_step, cron_expression="0 9 * * *")
        PersistentAgentStep.objects.filter(pk=trigger_step.pk).update(
            created_at=inbound.timestamp + timedelta(seconds=1),
        )

        context = _get_implied_send_context(self.agent)
        self.assertEqual(context["to_address"], build_web_user_address(self.user.id, self.agent.id))

        result = execute_send_chat_message(self.agent, {"body": "Cron report"})
        self.assertEqual(result["status"], "ok")
        outbound = PersistentAgentMessage.objects.get(body="Cron report")
        self.assertEqual(outbound.conversation.channel, CommsChannel.WEB)

    def test_webhook_run_uses_normal_owner_delivery_instead_of_reply_routing(self):
        start_web_session(self.agent, self.user)
        webhook_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.OTHER,
            address="webhook:test-source",
        )
        webhook_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.OTHER,
            address="webhook:test-source",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=webhook_endpoint,
            conversation=webhook_conversation,
            body='{"status":"ready"}',
            raw_payload={"source": "inbound_webhook", "source_kind": "webhook"},
        )

        scope = capture_inbound_routing_scope(self.agent, pending_inbound=False)
        self.assertIsNone(scope.message_id)
        self.assertEqual(
            _get_implied_send_context(self.agent)["to_address"],
            build_web_user_address(self.user.id, self.agent.id),
        )

        token = bind_inbound_routing_scope(scope)
        try:
            result = execute_send_chat_message(self.agent, {"body": "Webhook report"})
        finally:
            reset_inbound_routing_scope(token)

        self.assertEqual(result["status"], "ok")
        outbound = PersistentAgentMessage.objects.get(body="Webhook report")
        self.assertEqual(outbound.conversation.channel, CommsChannel.WEB)

    def test_pending_human_scope_ignores_a_newer_webhook(self):
        requester_message = self._add_inbound_web_message("Please answer me here.")
        webhook_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.OTHER,
            address="webhook:newer-source",
        )
        webhook_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.OTHER,
            address="webhook:newer-source",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            is_outbound=False,
            from_endpoint=webhook_endpoint,
            conversation=webhook_conversation,
            body='{"status":"ready"}',
            raw_payload={"source": "inbound_webhook", "source_kind": "webhook"},
        )

        scope = capture_inbound_routing_scope(self.agent, pending_inbound=True)

        self.assertIsNotNone(scope.message_id)
        self.assertEqual(scope.message_id, requester_message.id)

    def test_newer_peer_dm_does_not_leak_reply_to_historical_web_requester(self):
        self._add_inbound_web_message("An older web request.")
        peer_browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="peer-browser-for-implied-send",
        )
        peer = PersistentAgent.objects.create(
            user=self.user,
            name="Peer Agent",
            charter="Test peer charter",
            browser_use_agent=peer_browser_agent,
        )
        peer_link = AgentPeerLink.objects.create(
            agent_a=self.agent,
            agent_b=peer,
            created_by=self.user,
        )
        peer_conversation = PersistentAgentConversation.objects.create(
            owner_agent=self.agent,
            channel=CommsChannel.OTHER,
            address=f"peer:{peer.id}",
            peer_link=peer_link,
        )
        peer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=peer,
            channel=CommsChannel.OTHER,
            address=f"agent:{peer.id}",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=self.agent,
            peer_agent=peer,
            is_outbound=False,
            from_endpoint=peer_endpoint,
            conversation=peer_conversation,
            body="Can you send me the current status?",
        )
        start_web_session(self.agent, self.user)

        self.assertIsNone(_get_implied_send_context(self.agent))
        result = execute_send_chat_message(self.agent, {"body": "This belongs in the peer thread."})

        self.assertEqual(result["status"], "error")
        self.assertIn("peer DM", result["message"])
        self.assertIn("do not provide a web target", result["message"])
        self.assertIs(result["retryable"], False)
        self.assertFalse(PersistentAgentMessage.objects.filter(is_outbound=True).exists())

    def test_implied_send_ignores_hidden_session_after_visibility_grace(self):
        result = start_web_session(self.agent, self.user)
        PersistentAgentWebSession.objects.filter(pk=result.session.pk).update(
            is_visible=False,
            last_seen_at=timezone.now() - timedelta(seconds=30),
            last_visible_at=timezone.now() - timedelta(seconds=61),
        )

        context = _get_implied_send_context(self.agent)

        self.assertIsNone(context)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_allows_natural_progress_continuation_without_canonical_phrase(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        resp = self._mock_completion("Let me analyze this and send a summary.")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        self.assertGreaterEqual(len(mock_send_chat.call_args_list), 1)
        first_params = mock_send_chat.call_args_list[0][0][1]
        self.assertTrue(first_params.get("will_continue_work"))
        if len(mock_send_chat.call_args_list) > 1:
            second_params = mock_send_chat.call_args_list[1][0][1]
            self.assertIs(second_params.get("will_continue_work"), False)
        self.assertEqual(mock_completion.call_count, 2)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_request_human_input", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_keeps_free_text_question_as_chat_outside_planning(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        mock_request_human_input,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        resp = self._mock_completion(
            "Before I start researching, which target account segment should I research?\n\n"
            "- Enterprise\n"
            "- Mid-market"
        )
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_send_chat.assert_called_once()
        mock_request_human_input.assert_not_called()
        params = mock_send_chat.call_args[0][1]
        self.assertIn("which target account segment", params["body"])
        self.assertIs(params.get("will_continue_work"), False)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_request_human_input", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_keeps_would_you_like_question_as_chat_outside_planning(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        mock_request_human_input,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        resp = self._mock_completion(
            "Which target account segment would you like me to research?\n\n"
            "- Enterprise\n"
            "- Mid-market\n"
            "- SMB / Small business"
        )
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_send_chat.assert_called_once()
        mock_request_human_input.assert_not_called()
        params = mock_send_chat.call_args[0][1]
        self.assertIn("Which target account segment", params["body"])
        self.assertIs(params.get("will_continue_work"), False)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_request_human_input", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_chat_keeps_free_text_question_as_chat_outside_planning(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        mock_request_human_input,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        tool_call = MagicMock()
        tool_call.id = "call_send_chat"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "body": "Before I start monitoring, which competitors should I track?",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_send_chat.assert_called_once()
        mock_request_human_input.assert_not_called()
        params = mock_send_chat.call_args[0][1]
        self.assertEqual(params["body"], "Before I start monitoring, which competitors should I track?")
        self.assertIsNone(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_request_human_input", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_chat_source_request_stays_chat_outside_planning(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        mock_request_human_input,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        tool_call = MagicMock()
        tool_call.id = "call_send_chat"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "body": (
                    "I don't have any project status data available yet. Could you point me to where I "
                    "can find the latest project status? For example:\n\n"
                    "- A URL or document with the current status\n"
                    "- Or just tell me the key status details and who the client is"
                ),
            }
        )
        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_send_chat.assert_called_once()
        mock_request_human_input.assert_not_called()
        params = mock_send_chat.call_args[0][1]
        self.assertIn("project status data", params["body"])
        self.assertIsNone(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_uses_natural_continuation_when_open_plan_work(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Continue researching portfolio companies",
            status=PersistentAgentKanbanCard.Status.TODO,
        )

        resp = self._mock_completion("I've scraped the sites. Let me extract key details next.")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertTrue(params.get("will_continue_work"))

    def _run_loop_for_feedback_tool_choice(self, feedback):
        self._add_feedback_followup(
            feedback,
            initial_body="Draft outreach",
            prior_body="Here is the draft.",
        )
        start_web_session(self.agent, self.user)

        tools = [
            {"type": "function", "function": {"name": "sqlite_batch", "parameters": {"type": "object", "properties": {"will_continue_work": {"type": "boolean"}}}}},
            {"type": "function", "function": {"name": "send_chat_message", "parameters": {"type": "object", "properties": {"will_continue_work": {"type": "boolean"}}}}},
        ]
        failover_configs = [("provider-a", "model-a", {"supports_tool_choice": True, "temperature": 0.1})]
        prompt_result = (
            [{"role": "system", "content": "sys"}, {"role": "user", "content": feedback}],
            1000, None,
            {"prompt_failover_configs": failover_configs},
        )
        response = self._mock_completion(None, reasoning_content="Apply the feedback.")
        response.choices[0].message.tool_calls = []
        token_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "model": "model-a", "provider": "provider-a"}

        with patch.object(ep, "get_agent_tools", return_value=tools), \
             patch.object(ep, "get_agent_daily_credit_state", return_value=None), \
             patch.object(ep, "build_prompt_context", return_value=prompt_result), \
             patch.object(ep, "get_llm_config_with_failover", side_effect=AssertionError("use prompt configs")), \
             patch.object(ep, "_completion_with_failover", return_value=(response, token_usage)) as completion, \
             patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        return completion.call_args.kwargs

    def _tool_completion(self, name, arguments):
        response = self._mock_completion(None)
        response.choices[0].message.tool_calls = [
            {
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
        return response

    def _run_feedback_flow(self, feedback, responses, tools, *, prompt_metadata=None, config_apply=None):
        self._add_feedback_followup(
            feedback,
            initial_body="Draft outreach",
            prior_body="Here is the draft.",
        )
        start_web_session(self.agent, self.user)
        failover_configs = [("provider-a", "model-a", {"supports_tool_choice": True})]
        prompt_result = (
            [{"role": "system", "content": "sys"}, {"role": "user", "content": feedback}],
            1000,
            None,
            {"prompt_failover_configs": failover_configs, **(prompt_metadata or {})},
        )
        usage = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "model": "model-a",
            "provider": "provider-a",
        }
        executed_tools = []

        def execute_tool(_agent, *, tool_name, **_kwargs):
            executed_tools.append(tool_name)
            return {
                "status": "ok",
                "auto_sleep_ok": tool_name in ep.MESSAGE_TOOL_NAMES,
            }, None

        skill_apply = MagicMock(changed=False, errors=())
        with patch.object(ep, "get_agent_tools", return_value=tools), \
             patch.object(ep, "get_agent_daily_credit_state", return_value=None), \
             patch.object(ep, "build_prompt_context", return_value=prompt_result) as build_prompt, \
             patch.object(ep, "get_llm_config_with_failover", side_effect=AssertionError("use prompt configs")), \
             patch.object(ep, "_completion_with_failover", side_effect=[(response, usage) for response in responses]) as completion, \
             patch.object(ep, "_execute_tool_call_runtime", side_effect=execute_tool), \
             patch.object(ep, "_capture_tool_display_metadata", return_value={}), \
             patch.object(ep, "_ensure_credit_for_tool", return_value={"cost": None, "credit": None}), \
             patch.object(ep, "get_sqlite_db_path", return_value="/tmp/test-agent.sqlite3"), \
             patch.object(ep, "seed_sqlite_agent_config", return_value=MagicMock()), \
             patch.object(ep, "seed_sqlite_skills", return_value=MagicMock()), \
             patch.object(
                 ep,
                 "apply_sqlite_agent_config_updates",
                 return_value=config_apply or ep.AgentConfigApplyResult(updated_fields=("charter",), errors={}),
             ), \
             patch.object(ep, "apply_sqlite_skill_updates", return_value=skill_apply), \
             patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", len(responses)):
            ep._run_agent_loop(self.agent, is_first_run=False)

        return completion, build_prompt, executed_tools

    def test_successful_noop_charter_patch_confirms_without_retrying(self):
        tools = [
            {"type": "function", "function": {"name": "sqlite_batch", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "send_chat_message", "parameters": {"type": "object", "properties": {"body": {"type": "string"}, "will_continue_work": {"type": "boolean"}}}}},
        ]
        responses = [
            self._tool_completion("sqlite_batch", {
                "target_charter_text": "",
                "replacement_charter_text": "Write naturally.",
            }),
            self._tool_completion("send_chat_message", {
                "body": "Got it. I'll keep the writing natural.",
                "will_continue_work": False,
            }),
        ]

        completion, _build_prompt, executed_tools = self._run_feedback_flow(
            "You sound robotic.",
            responses,
            tools,
            config_apply=ep.AgentConfigApplyResult(updated_fields=(), errors={}),
        )

        self.assertEqual(executed_tools, ["sqlite_batch", "send_chat_message"])
        self.assertEqual(completion.call_count, 2)

    def test_charter_patch_requires_confirmed_config_result_before_replying(self):
        tools = [
            {"type": "function", "function": {"name": "sqlite_batch", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "send_chat_message", "parameters": {"type": "object", "properties": {"body": {"type": "string"}, "will_continue_work": {"type": "boolean"}}}}},
        ]
        responses = [
            self._tool_completion("sqlite_batch", {
                "target_charter_text": "",
                "replacement_charter_text": "Write naturally.",
            }),
            self._tool_completion("send_chat_message", {
                "body": "Got it. I'll keep the writing natural.",
                "will_continue_work": False,
            }),
        ]

        completion, _build_prompt, executed_tools = self._run_feedback_flow(
            "You sound robotic.",
            responses,
            tools,
            config_apply=ep.AgentConfigApplyResult(
                updated_fields=(),
                errors={"charter": "Charter update failed."},
            ),
        )

        self.assertEqual(executed_tools, ["sqlite_batch"])
        self.assertEqual(completion.call_count, 2)

    def test_charter_patch_confirmation_requires_agent_config_metadata(self):
        self.assertFalse(ep._tool_result_confirms_charter_patch({"status": "ok"}))
        self.assertFalse(ep._tool_result_confirms_charter_patch({
            "status": "error",
            "agent_config_update": {"unchanged_fields": ["charter"], "errors": {}},
        }))
        self.assertFalse(ep._tool_result_confirms_charter_patch({
            "status": "ok",
            "agent_config_update": {
                "updated_fields": [],
                "unchanged_fields": ["charter"],
                "errors": {"charter": "Rejected"},
            },
        }))
        self.assertTrue(ep._tool_result_confirms_charter_patch({
            "status": "ok",
            "agent_config_update": {
                "updated_fields": ["charter"],
                "unchanged_fields": [],
                "errors": {},
            },
        }))
        self.assertTrue(ep._tool_result_confirms_charter_patch({
            "status": "ok",
            "agent_config_update": {
                "updated_fields": [],
                "unchanged_fields": ["charter"],
                "errors": {},
            },
        }))

    def test_run_loop_forces_only_sqlite_for_durable_feedback(self):
        completion_kwargs = self._run_loop_for_feedback_tool_choice("You sound robotic.")

        self.assertEqual(len(completion_kwargs["tools"]), 1)
        sqlite_tool = completion_kwargs["tools"][0]["function"]
        self.assertEqual(sqlite_tool["name"], "sqlite_batch")
        self.assertIn('CURRENT CHARTER (<charter>), the only source for a nonempty target: "Test charter"', sqlite_tool["description"])
        self.assertIn("Patch ONLY the lasting clauses", sqlite_tool["description"])
        self.assertIn("smallest exact contiguous span", sqlite_tool["description"])
        self.assertIn("not copied feedback or prior output", sqlite_tool["description"])
        self.assertEqual(
            set(sqlite_tool["parameters"]["properties"]),
            {"target_charter_text", "replacement_charter_text"},
        )
        self.assertEqual(
            sqlite_tool["parameters"]["required"],
            ["target_charter_text", "replacement_charter_text"],
        )
        self.assertIs(sqlite_tool["parameters"]["additionalProperties"], False)
        self.assertNotIn("sql", sqlite_tool["parameters"]["properties"])
        self.assertEqual(completion_kwargs["messages"][0], {"role": "system", "content": "sys"})
        focused_feedback = completion_kwargs["messages"][1]["content"]
        self.assertIn("<charter>Test charter</charter>", focused_feedback)
        self.assertIn("You sound robotic.", focused_feedback)
        self.assertIn("<prior_output_context>Here is the draft.</prior_output_context>", focused_feedback)
        self.assertIsNone(completion_kwargs["stream_broadcaster"])
        self.assertEqual(completion_kwargs["failover_configs"][0][2]["tool_choice"], {"type": "function", "function": {"name": "sqlite_batch"}})
        self.assertIs(completion_kwargs["failover_configs"][0][2]["use_parallel_tool_calls"], False)

    def test_focused_charter_history_excludes_temporary_clauses(self):
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "For this renewal only, put legal review first. Going forward, send outcomes."},
        ]

        focused = ep._focused_charter_patch_history(
            history,
            "Coordinate renewals.",
            ("Going forward, send outcomes.",),
            "I will keep you posted.",
        )

        self.assertEqual(focused[0], history[0])
        self.assertIn("Going forward, send outcomes.", focused[1]["content"])
        self.assertNotIn("legal review first", focused[1]["content"])

    def test_source_reconciliation_contract_is_promoted_without_hiding_context(self):
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": (
                "Review Aster Labs. [SOURCE ARRAYS result_id=abc123; stored paths: "
                "$.content.accounts(account_id,name).]"
            )},
        ]

        promoted = ep._promote_source_reconciliation_history(history)

        self.assertIn("Current Fresh Source Contract", promoted[0]["content"])
        self.assertIn("result_id=abc123", promoted[0]["content"])
        self.assertEqual(promoted[1], history[1])
        self.assertEqual(ep._promote_source_reconciliation_history([
            {"role": "system", "content": "system"},
            {"role": "user", "content": "ordinary task"},
        ])[0]["content"], "system")

    def test_run_loop_forces_source_focused_sqlite_without_streamed_or_implied_prose(self):
        tools = [
            {"type": "function", "function": {"name": "sqlite_batch", "description": "Original SQLite contract", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "http_request", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "send_chat_message", "parameters": {"type": "object", "properties": {}}}},
        ]
        response = self._tool_completion("sqlite_batch", {"sql": (
            "INSERT INTO accounts(account_id,name) SELECT json_extract(j.value,'$.account_id'),"
            "json_extract(j.value,'$.name') FROM __tool_results r,"
            "json_each(r.result_json,'$.content.accounts') j WHERE r.result_id='abc123' "
            "ON CONFLICT(account_id) DO UPDATE SET name=excluded.name; SELECT * FROM accounts"
        )})
        response.choices[0].message.content = "I found the fresh accounts."

        completion, _, executed_tools = self._run_feedback_flow(
            "Review the fresh CRM snapshot.",
            [response],
            tools,
            prompt_metadata={
                "source_reconciliation_directive": "[SOURCE ARRAYS result_id=abc123; stored paths: $.content.accounts(account_id,name).]",
                "prompt_allows_implied_send": True,
            },
        )

        kwargs = completion.call_args.kwargs
        self.assertEqual(executed_tools, ["sqlite_batch"])
        self.assertEqual([tool["function"]["name"] for tool in kwargs["tools"]], ["sqlite_batch"])
        description = kwargs["tools"][0]["function"]["description"]
        self.assertNotIn("Original SQLite contract", description)
        self.assertIn("$.content.accounts(account_id,name)", description)
        self.assertEqual(
            kwargs["tools"][0]["function"]["parameters"]["properties"]["will_continue_work"]["const"],
            True,
        )
        sqlite_parameters = kwargs["tools"][0]["function"]["parameters"]
        self.assertEqual(sqlite_parameters["required"], ["sql", "will_continue_work"])
        self.assertIn("result_id=abc123", sqlite_parameters["properties"]["sql"]["description"])
        self.assertNotIn("queries", sqlite_parameters["properties"])
        self.assertEqual(
            kwargs["failover_configs"][0][2]["tool_choice"],
            {"type": "function", "function": {"name": "sqlite_batch"}},
        )
        self.assertIs(kwargs["failover_configs"][0][2]["use_parallel_tool_calls"], False)
        self.assertIsNone(kwargs["stream_broadcaster"])
        self.assertIs(kwargs["allow_streamed_content"], False)

    def test_direct_rewrite_continues_to_distinct_research_task(self):
        feedback = "That sounded robotic. Rewrite it, then find three prospects."
        analysis = ep._analyze_feedback_turn(feedback, "Here is the draft.")
        self.assertTrue(analysis.direct_reply_task)
        self.assertTrue(analysis.separate_task)

        tools = [
            {"type": "function", "function": {"name": "sqlite_batch", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "send_chat_message", "parameters": {"type": "object", "properties": {"body": {"type": "string"}, "will_continue_work": {"type": "boolean"}}}}},
            {"type": "function", "function": {"name": "search_tools", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
        ]
        responses = [
            self._tool_completion(
                "sqlite_batch",
                {
                    "target_charter_text": "Test charter",
                    "replacement_charter_text": "Test charter. Write naturally.",
                },
            ),
            self._tool_completion(
                "send_chat_message",
                {"body": "Here is the rewritten draft.", "will_continue_work": True},
            ),
            self._tool_completion("search_tools", {"query": "prospect research"}),
        ]

        completion, build_prompt, executed_tools = self._run_feedback_flow(feedback, responses, tools)

        self.assertEqual(executed_tools, ["sqlite_batch", "send_chat_message", "search_tools"])
        reply_tool = completion.call_args_list[1].kwargs["tools"][0]["function"]
        self.assertEqual(reply_tool["name"], "send_chat_message")
        self.assertIs(reply_tool["parameters"]["properties"]["will_continue_work"]["const"], True)
        self.assertIn(
            "Execute the remaining explicit request now",
            build_prompt.call_args_list[2].kwargs["continuation_notice"],
        )

    def test_direct_rewrite_with_email_task_does_not_send_rewrite_to_web_chat(self):
        feedback = "That sounded robotic. Rewrite it. Email it to Sarah."
        analysis = ep._analyze_feedback_turn(feedback, "Here is the draft.")
        self.assertTrue(analysis.direct_reply_task)
        self.assertTrue(analysis.separate_task)

        tools = [
            {"type": "function", "function": {"name": "sqlite_batch", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "send_chat_message", "parameters": {"type": "object", "properties": {"body": {"type": "string"}, "will_continue_work": {"type": "boolean"}}}}},
            {"type": "function", "function": {"name": "send_email", "parameters": {"type": "object", "properties": {"to_address": {"type": "string"}, "subject": {"type": "string"}, "mobile_first_html": {"type": "string"}, "will_continue_work": {"type": "boolean"}}}}},
        ]
        responses = [
            self._tool_completion(
                "sqlite_batch",
                {
                    "target_charter_text": "Test charter",
                    "replacement_charter_text": "Test charter. Write naturally.",
                },
            ),
            self._tool_completion(
                "send_email",
                {
                    "to_address": "sarah@example.com",
                    "subject": "Rewritten note",
                    "mobile_first_html": "<p>Here is the rewritten draft.</p>",
                    "will_continue_work": False,
                },
            ),
        ]

        completion, build_prompt, executed_tools = self._run_feedback_flow(feedback, responses, tools)

        self.assertEqual(executed_tools, ["sqlite_batch", "send_email"])
        second_tool_names = {
            tool["function"]["name"] for tool in completion.call_args_list[1].kwargs["tools"]
        }
        self.assertEqual(second_tool_names, {"sqlite_batch", "send_chat_message", "send_email"})
        self.assertIn(
            "Execute the remaining explicit request now",
            build_prompt.call_args_list[1].kwargs["continuation_notice"],
        )

    def test_run_loop_forces_terminal_same_channel_reply_for_temporary_feedback(self):
        completion_kwargs = self._run_loop_for_feedback_tool_choice(
            "Only this batch, these long updates aren't useful."
        )

        self.assertEqual(len(completion_kwargs["tools"]), 1)
        reply_tool = completion_kwargs["tools"][0]["function"]
        self.assertEqual(reply_tool["name"], "send_chat_message")
        self.assertIn("feedback acknowledgement only", reply_tool["description"])
        self.assertIn("exactly one sentence", reply_tool["description"])
        self.assertIn("will_continue_work=false", reply_tool["description"])
        self.assertIs(reply_tool["parameters"]["properties"]["will_continue_work"]["const"], False)
        self.assertIsNone(completion_kwargs["stream_broadcaster"])
        self.assertEqual(
            completion_kwargs["failover_configs"][0][2]["tool_choice"],
            {"type": "function", "function": {"name": "send_chat_message"}},
        )

    def test_temporary_feedback_with_rewrite_request_is_not_reduced_to_acknowledgement(self):
        completion_kwargs = self._run_loop_for_feedback_tool_choice(
            "For this message, that tone is too formal. Rewrite it."
        )

        self.assertEqual(
            {tool["function"]["name"] for tool in completion_kwargs["tools"]},
            {"sqlite_batch", "send_chat_message"},
        )
        self.assertNotIn("tool_choice", completion_kwargs["failover_configs"][0][2])

@tag("batch_event_processing_credits")
class DailyLimitMessageOnlyModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="daily-limit-mode@example.com",
            email="daily-limit-mode@example.com",
            password="password",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

    def setUp(self):
        self.task_credit_patcher = patch(
            "api.models.TaskCreditService.check_and_consume_credit_for_owner",
            return_value={"success": True, "credit": None},
        )
        self.task_credit_patcher.start()
        self.addCleanup(self.task_credit_patcher.stop)
        self.follow_up_patcher = patch("api.agent.core.event_processing._schedule_agent_follow_up")
        self.follow_up_patcher.start()
        self.addCleanup(self.follow_up_patcher.stop)

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="browser-agent-for-daily-limit-mode",
        )
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="Daily Limit Mode Agent",
            charter="Test charter",
            browser_use_agent=browser_agent,
        )

    def _daily_limit_state(self):
        return {
            "hard_limit": Decimal("2"),
            "hard_limit_remaining": Decimal("0"),
            "soft_target": Decimal("1"),
            "soft_target_remaining": Decimal("0"),
            "used": Decimal("2"),
            "next_reset": timezone.now(),
        }

    def _tool_definition(self, name: str) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def _completion(self, *, content=None, tool_calls=None):
        msg = MagicMock()
        msg.tool_calls = tool_calls
        msg.function_call = None
        msg.content = content
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def _mock_completion(self, content, *, reasoning_content=None):
        msg = MagicMock()
        msg.tool_calls = None
        msg.function_call = None
        msg.content = content
        if reasoning_content is not None:
            msg.reasoning_content = reasoning_content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def _tool_call(self, name: str, arguments: dict) -> MagicMock:
        tool_call = MagicMock()
        tool_call.id = f"call_{name}"
        tool_call.function = MagicMock()
        tool_call.function.name = name
        tool_call.function.arguments = json.dumps(arguments)
        return tool_call

    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing.get_agent_daily_credit_state")
    @patch("api.agent.core.event_processing.get_agent_tools")
    def test_daily_limit_mode_filters_tool_list_to_message_tools(
        self,
        mock_get_tools,
        mock_get_daily_state,
        mock_build_prompt,
    ):
        start_web_session(self.agent, self.user)
        mock_get_tools.return_value = [
            self._tool_definition("send_email"),
            self._tool_definition("send_sms"),
            self._tool_definition("send_chat_message"),
            self._tool_definition("send_agent_message"),
            self._tool_definition("sleep_until_next_trigger"),
            self._tool_definition("sqlite_query"),
        ]
        mock_get_daily_state.return_value = self._daily_limit_state()
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)
        observed_tool_names: list[str] = []

        def _capture_completion(*_args, **kwargs):
            observed_tool_names.extend(
                tool["function"]["name"]
                for tool in kwargs["tools"]
            )
            return (
                self._completion(content=None, tool_calls=None),
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._completion_with_failover",
            side_effect=_capture_completion,
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(
            observed_tool_names,
            [
                "send_email",
                "send_sms",
                "send_chat_message",
                "send_agent_message",
                "sleep_until_next_trigger",
            ],
        )

    @patch("api.agent.core.event_processing.execute_enabled_tool")
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing.get_agent_daily_credit_state")
    @patch("api.agent.core.event_processing.get_agent_tools")
    def test_daily_limit_mode_rejects_non_message_tool_calls(
        self,
        mock_get_tools,
        mock_get_daily_state,
        mock_build_prompt,
        mock_execute_enabled_tool,
    ):
        mock_get_tools.return_value = [self._tool_definition("send_email")]
        mock_get_daily_state.return_value = self._daily_limit_state()
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)
        completion = self._completion(
            tool_calls=[self._tool_call("sqlite_query", {"query": "select 1"})]
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._schedule_agent_follow_up",
        ), patch(
            "api.agent.core.event_processing._completion_with_failover",
            return_value=(
                completion,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_execute_enabled_tool.assert_not_called()
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__contains="Only message and sleep tools are allowed right now",
        ).first()
        self.assertIsNotNone(correction_step)

    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch(
        "api.agent.core.event_processing.TaskCreditService.check_and_consume_credit_for_owner",
        return_value={"success": True, "credit": None},
    )
    @patch(
        "api.agent.core.event_processing.TaskCreditService.calculate_available_tasks_for_owner",
        return_value=Decimal("5"),
    )
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing.get_agent_daily_credit_state")
    @patch("api.agent.core.event_processing.get_agent_tools")
    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    def test_daily_limit_mode_executes_send_email_without_consuming_credit(
        self,
        mock_get_tools,
        mock_get_daily_state,
        mock_build_prompt,
        _mock_available,
        mock_consume,
        mock_send_email,
    ):
        mock_get_tools.return_value = [self._tool_definition("send_email")]
        mock_get_daily_state.return_value = self._daily_limit_state()
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)
        completion = self._completion(
            tool_calls=[
                self._tool_call(
                    "send_email",
                    {
                        "to_address": "owner@example.com",
                        "subject": "Daily limit reached",
                        "mobile_first_html": "<p>Please raise the limit.</p>",
                    },
                )
            ]
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._completion_with_failover",
            return_value=(
                completion,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_send_email.assert_called_once()
        mock_consume.assert_not_called()
        tool_call = PersistentAgentToolCall.objects.filter(
            step__agent=self.agent,
            tool_name="send_email",
        ).order_by("-step_id").first()
        self.assertIsNotNone(tool_call)
        self.assertIsNone(tool_call.step.credits_cost)
        self.assertIsNone(tool_call.step.completion_id)

    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch(
        "api.agent.core.event_processing.TaskCreditService.check_and_consume_credit_for_owner",
        return_value={"success": True, "credit": None},
    )
    @patch(
        "api.agent.core.event_processing.TaskCreditService.calculate_available_tasks_for_owner",
        return_value=Decimal("0"),
    )
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing.get_agent_daily_credit_state", return_value={})
    @patch("api.agent.core.event_processing.get_agent_tools")
    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    def test_task_credit_mode_filters_tools_and_sends_without_billing(
        self,
        mock_get_tools,
        _mock_get_daily_state,
        mock_build_prompt,
        _mock_available,
        mock_consume,
        mock_send_email,
    ):
        mock_get_tools.return_value = [
            self._tool_definition("send_email"),
            self._tool_definition("sleep_until_next_trigger"),
            self._tool_definition("sqlite_query"),
        ]
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)
        completion = self._completion(
            tool_calls=[
                self._tool_call(
                    "send_email",
                    {
                        "to_address": "owner@example.com",
                        "subject": "Task credits exhausted",
                        "mobile_first_html": "<p>Please restore task credits.</p>",
                    },
                )
            ]
        )
        observed_tool_names = []

        def _capture_completion(*_args, **kwargs):
            observed_tool_names.extend(tool["function"]["name"] for tool in kwargs["tools"])
            return (
                completion,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._completion_with_failover",
            side_effect=_capture_completion,
        ):
            ep._run_agent_loop(
                self.agent,
                is_first_run=False,
                credit_snapshot={
                    "available": Decimal("0"),
                    "daily_state": {},
                    "refresh_task_credits": True,
                },
            )

        self.assertEqual(
            observed_tool_names,
            ["send_email", "sleep_until_next_trigger"],
        )
        mock_build_prompt.assert_called_once()
        self.assertEqual(mock_build_prompt.call_args.kwargs["task_credit_available"], Decimal("0"))
        mock_send_email.assert_called_once()
        mock_consume.assert_not_called()
        tool_call = PersistentAgentToolCall.objects.get(
            step__agent=self.agent,
            tool_name="send_email",
        )
        self.assertIsNone(tool_call.step.credits_cost)
        self.assertIsNone(tool_call.step.completion_id)

    @patch(
        "api.agent.core.event_processing.TaskCreditService.check_and_consume_credit_for_owner",
        return_value={"success": True, "credit": None},
    )
    @patch(
        "api.agent.core.event_processing.TaskCreditService.calculate_available_tasks_for_owner",
        return_value=Decimal("0"),
    )
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing.get_agent_daily_credit_state")
    @patch("api.agent.core.event_processing.get_agent_tools")
    @patch("api.agent.core.event_processing.settings.GOBII_PROPRIETARY_MODE", True)
    def test_daily_limit_mode_allows_sleep_without_consuming_credit(
        self,
        mock_get_tools,
        mock_get_daily_state,
        mock_build_prompt,
        _mock_available,
        mock_consume,
    ):
        mock_get_tools.return_value = [self._tool_definition("sleep_until_next_trigger")]
        mock_get_daily_state.return_value = self._daily_limit_state()
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)
        completion = self._completion(
            tool_calls=[self._tool_call("sleep_until_next_trigger", {})]
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._completion_with_failover",
            return_value=(
                completion,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_consume.assert_not_called()
        sleep_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description="Decided to sleep until next trigger.",
        ).order_by("-id").first()
        self.assertIsNotNone(sleep_step)
        self.assertIsNone(sleep_step.credits_cost)
        self.assertIsNone(sleep_step.task_credit)

    @patch("api.agent.core.event_processing._should_imply_continue", return_value=False)
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_rechecks_open_plan_before_sleep(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
        _mock_should_imply_continue,
    ):
        """A conservative implied-stop decision should still continue on clear progress language."""
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)
        PersistentAgentKanbanCard.objects.create(
            assigned_agent=self.agent,
            title="Continue researching portfolio companies",
            status=PersistentAgentKanbanCard.Status.TODO,
        )

        first_resp = self._mock_completion("I've scraped the profiles. Let me extract key details next.")
        second_resp = self._mock_completion(None)

        mock_completion.side_effect = [
            (
                first_resp,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
            (
                second_resp,
                {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ]

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertTrue(params.get("will_continue_work"))
        self.assertEqual(mock_completion.call_count, 2)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_strips_canonical_continuation_phrase(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        resp = self._mock_completion(f"Here is the summary.\n{ep.CANONICAL_CONTINUATION_PHRASE}")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertEqual(params.get("body"), "Here is the summary.")
        self.assertTrue(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_enabled_tool", return_value={"status": "ok"})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_with_tool_followup_continues_without_canonical_phrase(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_enabled_tool,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        start_web_session(self.agent, self.user)

        tool_call = MagicMock()
        tool_call.id = "call_dummy"
        tool_call.function = MagicMock()
        tool_call.function.name = "dummy_tool"
        tool_call.function.arguments = "{}"

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = "Got it, I'll dig in and report back."
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        followup_msg = MagicMock()
        followup_msg.tool_calls = None
        followup_msg.function_call = None
        followup_msg.content = None
        followup_choice = MagicMock()
        followup_choice.message = followup_msg
        followup_resp = MagicMock()
        followup_resp.choices = [followup_choice]

        mock_completion.side_effect = [
            (
                resp,
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "model": "m",
                    "provider": "p",
                },
            ),
            (
                followup_resp,
                {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                    "model": "m",
                    "provider": "p",
                },
            ),
        ]

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 2):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        self.assertEqual(mock_completion.call_count, 2)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_strips_canonical_continuation_phrase(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_123"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": f"Here is the summary.\n{ep.CANONICAL_CONTINUATION_PHRASE}",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._schedule_agent_follow_up",
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)
        params = mock_send_chat.call_args[0][1]
        self.assertEqual(params.get("body"), "Here is the summary.")
        self.assertTrue(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_infers_continue_for_progress_update_without_flag(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        """Progress-update explicit sends should continue even if the flag is omitted."""
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_456"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "Great question! Let me dig into the most-discussed stories and find some standout comments.",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._schedule_agent_follow_up",
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        params = mock_send_chat.call_args[0][1]
        self.assertTrue(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_keeps_stop_for_defer_language_without_flag(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_defer_1"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "I'll wait here. Let me know if you need anything else.",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        params = mock_send_chat.call_args[0][1]
        self.assertIsNone(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_keeps_stop_for_completion_language_without_flag(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_done_1"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "All done. Here's what I found.",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        params = mock_send_chat.call_args[0][1]
        self.assertIsNone(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_request_human_input", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_keeps_stop_when_message_asks_user_question_without_flag(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        mock_request_human_input,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        tool_call = MagicMock()
        tool_call.id = "call_send_q_1"
        tool_call.function = MagicMock()
        tool_call.function.name = "send_chat_message"
        tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "Can you share which thread you care about most?",
            }
        )

        msg = MagicMock()
        msg.tool_calls = [tool_call]
        msg.function_call = None
        msg.content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1), patch(
            "api.agent.core.event_processing._schedule_agent_follow_up",
        ):
            ep._run_agent_loop(self.agent, is_first_run=False)

        mock_send_chat.assert_called_once()
        mock_request_human_input.assert_not_called()
        params = mock_send_chat.call_args[0][1]
        self.assertIsNone(params.get("will_continue_work"))

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_uses_last_chat_message_without_active_session(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        mock_send_email,
        _mock_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        prior_step = PersistentAgentStep.objects.create(
            agent=self.agent,
            description="Tool call: send_chat_message",
        )
        PersistentAgentToolCall.objects.create(
            step=prior_step,
            tool_name="send_chat_message",
            tool_params={
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "old",
            },
            result="{}",
        )

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="owner@example.com",
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        resp = self._mock_completion("Hello fallback")
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertFalse(mock_send_chat.called)
        self.assertFalse(mock_send_email.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNotNone(correction_step)

    @patch("api.agent.core.event_processing.execute_send_email", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_failure_persists_reasoning_step(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_email,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.EMAIL,
            address="owner@example.com",
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        resp = self._mock_completion(
            "Hello without destination",
            reasoning_content="Need explicit send destination.",
        )
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        reasoning_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith=INTERNAL_REASONING_PREFIX,
        ).first()
        self.assertIsNotNone(reasoning_step)
        self.assertIn("Need explicit send destination.", reasoning_step.description)

        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNotNone(correction_step)
        self.assertFalse(mock_send_email.called)

    @patch("api.agent.core.event_processing.get_llm_config_with_failover", return_value=[("mock", "mock-model", {})])
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_reasoning_only_content_list_continues_then_auto_sleeps(
        self,
        mock_completion,
        mock_build_prompt,
        _mock_llm_config,
    ):
        """Thinking-only responses continue up to MAX_NO_TOOL_STREAK before auto-sleeping."""
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        msg = MagicMock()
        msg.tool_calls = []
        msg.function_call = None
        msg.content = [{"type": "thinking", "text": "Plan the response."}]
        msg.reasoning_content = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 5):
            ep._run_agent_loop(self.agent, is_first_run=False)

        # Should be called MAX_NO_TOOL_STREAK times before auto-sleeping
        # (thinking content doesn't cause immediate stop; streak limit does)
        self.assertEqual(mock_completion.call_count, ep.MAX_NO_TOOL_STREAK)

        reasoning_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith=INTERNAL_REASONING_PREFIX,
        ).first()
        self.assertIsNotNone(reasoning_step)
        self.assertIn("Plan the response.", reasoning_step.description)

        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNone(correction_step)

    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_failure_still_executes_other_tool_calls(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
    ):
        """When implied send fails due to no web session, other tool calls should still execute."""
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None)

        # Set up a preferred endpoint that is NOT a web session (so implied send will fail)
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=build_web_user_address(self.user.id, self.agent.id),
            owner_agent=None,
        )
        self.agent.preferred_contact_endpoint = endpoint
        self.agent.save(update_fields=["preferred_contact_endpoint"])

        # Create a mock response with BOTH message content AND a tool call
        msg = MagicMock()
        msg.content = "Hello, this message should be dropped"
        msg.reasoning_content = None
        # Add a sleep tool call (simple tool that doesn't require mocking external services)
        sleep_tool_call = MagicMock()
        sleep_tool_call.id = "call_sleep_123"
        sleep_tool_call.function = MagicMock()
        sleep_tool_call.function.name = "sleep_until_next_trigger"
        sleep_tool_call.function.arguments = "{}"
        msg.tool_calls = [sleep_tool_call]

        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        # Implied send should NOT have been called (no active web session)
        self.assertFalse(mock_send_chat.called)

        # The correction step should exist (notifying agent that message was dropped)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNotNone(correction_step)

        # The sleep tool call should still have been executed (creating a step)
        sleep_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description="Decided to sleep until next trigger.",
        ).first()
        self.assertIsNotNone(sleep_step, "Other tool calls should execute even when implied send fails")

    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner", return_value={"success": True, "credit": None})
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    @patch("api.agent.core.event_processing.get_llm_config_with_failover")
    def test_run_loop_uses_prompt_resolved_failover_configs(
        self,
        mock_get_llm_config,
        mock_completion,
        mock_build_prompt,
        _mock_send_chat,
        _mock_credit,
        _mock_task_credit,
    ):
        prompt_failover_configs = [
            ("provider-a", "openai/gpt-4o-mini", {"allow_implied_send": True}),
            ("provider-b", "openai/gpt-4.1-mini", {"allow_implied_send": False}),
        ]
        mock_build_prompt.return_value = (
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            1000,
            None,
            {
                "prompt_allows_implied_send": False,
                "prompt_failover_configs": prompt_failover_configs,
            },
        )
        mock_get_llm_config.side_effect = AssertionError("loop should use prompt-resolved failover configs")

        msg = MagicMock()
        msg.content = ""
        msg.reasoning_content = None
        sleep_tool_call = MagicMock()
        sleep_tool_call.function = MagicMock()
        sleep_tool_call.function.name = "sleep_until_next_trigger"
        sleep_tool_call.function.arguments = "{}"
        msg.tool_calls = [sleep_tool_call]

        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        resp.model_extra = {"usage": MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)}
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertEqual(
            mock_completion.call_args.kwargs["failover_configs"],
            prompt_failover_configs,
        )

    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner", return_value={"success": True, "credit": None})
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_implied_send_respects_selected_model_opt_out(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
        _mock_task_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None, {"prompt_allows_implied_send": True})
        start_web_session(self.agent, self.user)

        resp = self._mock_completion("Model says hello")
        resp.model_extra = {"gobii_runtime_hints": {"allow_implied_send": False}}
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNotNone(correction_step)

    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner", return_value={"success": True, "credit": None})
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_explicit_send_still_executes_when_implied_send_disabled(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
        _mock_task_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None, {"prompt_allows_implied_send": False})
        start_web_session(self.agent, self.user)

        msg = MagicMock()
        msg.content = ""
        msg.reasoning_content = None
        send_tool_call = MagicMock()
        send_tool_call.id = "call_send_123"
        send_tool_call.function = MagicMock()
        send_tool_call.function.name = "send_chat_message"
        send_tool_call.function.arguments = json.dumps(
            {
                "to_address": build_web_user_address(self.user.id, self.agent.id),
                "body": "Explicit send works",
            }
        )
        msg.tool_calls = [send_tool_call]

        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        resp.model_extra = {"gobii_runtime_hints": {"allow_implied_send": False}}
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertTrue(mock_send_chat.called)

    @patch("api.models.TaskCreditService.check_and_consume_credit_for_owner", return_value={"success": True, "credit": None})
    @patch("api.agent.core.event_processing._ensure_credit_for_tool", return_value={"cost": None, "credit": None})
    @patch("api.agent.core.event_processing.execute_send_chat_message", return_value={"status": "ok", "auto_sleep_ok": True})
    @patch("api.agent.core.event_processing.build_prompt_context")
    @patch("api.agent.core.event_processing._completion_with_failover")
    def test_prompt_side_opt_out_blocks_implied_send_even_if_selected_model_allows_it(
        self,
        mock_completion,
        mock_build_prompt,
        mock_send_chat,
        _mock_credit,
        _mock_task_credit,
    ):
        mock_build_prompt.return_value = ([{"role": "system", "content": "sys"}], 1000, None, {"prompt_allows_implied_send": False})
        start_web_session(self.agent, self.user)

        resp = self._mock_completion("Prompt-side opt-out should win")
        resp.model_extra = {"gobii_runtime_hints": {"allow_implied_send": True}}
        mock_completion.return_value = (
            resp,
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "model": "m",
                "provider": "p",
            },
        )

        with patch.object(ep, "MAX_AGENT_LOOP_ITERATIONS", 1):
            ep._run_agent_loop(self.agent, is_first_run=False)

        self.assertFalse(mock_send_chat.called)
        correction_step = PersistentAgentStep.objects.filter(
            agent=self.agent,
            description__startswith="The answer below was not delivered.",
        ).first()
        self.assertIsNotNone(correction_step)


@tag("batch_event_processing_credits")
class ContinuationSignalTests(TestCase):
    """Tests for the _has_continuation_signal helper function."""

    def test_has_continuation_signal_with_let_me(self):
        self.assertTrue(ep._has_continuation_signal("Let me check that for you."))

    def test_has_continuation_signal_with_ill(self):
        self.assertTrue(ep._has_continuation_signal("I'll compile a report now."))

    def test_has_continuation_signal_with_im_going_to(self):
        self.assertTrue(ep._has_continuation_signal("I'm going to fetch the data."))

    def test_has_continuation_signal_case_insensitive(self):
        self.assertTrue(ep._has_continuation_signal("LET ME DO THAT"))
        self.assertTrue(ep._has_continuation_signal("i'll work on it"))

    def test_has_continuation_signal_false_for_done(self):
        self.assertFalse(ep._has_continuation_signal("Work complete."))
        self.assertFalse(ep._has_continuation_signal("That's everything you asked for."))

    def test_has_continuation_signal_empty(self):
        self.assertFalse(ep._has_continuation_signal(""))
        self.assertFalse(ep._has_continuation_signal(None))

    def test_has_continuation_signal_with_working_on(self):
        self.assertTrue(ep._has_continuation_signal("I'm currently working on the analysis."))

    def test_has_continuation_signal_with_proceeding_to(self):
        self.assertTrue(ep._has_continuation_signal("Proceeding to extract the data."))


@tag("batch_event_processing_credits")
class CompletionSignalTests(TestCase):
    """Tests for the _has_completion_signal helper function."""

    def test_has_completion_signal_with_work_complete(self):
        self.assertTrue(ep._has_completion_signal("Work complete."))
        self.assertTrue(ep._has_completion_signal("Work complete"))

    def test_has_completion_signal_with_task_complete(self):
        self.assertTrue(ep._has_completion_signal("Task complete! Here's the report."))

    def test_has_completion_signal_with_all_done(self):
        self.assertTrue(ep._has_completion_signal("All done! Let me know if you need anything else."))

    def test_has_completion_signal_with_thats_everything(self):
        self.assertTrue(ep._has_completion_signal("That's everything you asked for."))

    def test_has_completion_signal_with_here_are_your_results(self):
        self.assertTrue(ep._has_completion_signal("Here are your results: ..."))

    def test_has_completion_signal_with_heres_what_i_found(self):
        self.assertTrue(ep._has_completion_signal("Here's what I found in the data."))

    def test_has_completion_signal_case_insensitive(self):
        self.assertTrue(ep._has_completion_signal("WORK COMPLETE"))
        self.assertTrue(ep._has_completion_signal("all done"))

    def test_has_completion_signal_false_for_continuation(self):
        self.assertFalse(ep._has_completion_signal("Let me check that."))
        self.assertFalse(ep._has_completion_signal("I'll get that for you."))
        self.assertFalse(ep._has_completion_signal(ep.CANONICAL_CONTINUATION_PHRASE))

    def test_has_completion_signal_empty(self):
        self.assertFalse(ep._has_completion_signal(""))
        self.assertFalse(ep._has_completion_signal(None))

    def test_has_completion_signal_with_that_completes(self):
        self.assertTrue(ep._has_completion_signal("That completes the analysis."))

    def test_has_completion_signal_with_this_completes(self):
        self.assertTrue(ep._has_completion_signal("This completes your request."))


@tag("batch_event_processing_credits")
class MessageContinuationInferenceTests(TestCase):
    """Unit tests for omitted will_continue_work inference on message tools."""

    def test_infer_continuation_true_for_progress_update(self):
        self.assertTrue(
            ep._should_infer_message_tool_continuation(
                "Great question! Let me dig into the most-discussed stories first."
            )
        )

    def test_infer_continuation_false_for_completion_signal(self):
        self.assertFalse(
            ep._should_infer_message_tool_continuation(
                "All done. Here's what I found."
            )
        )

    def test_infer_continuation_false_for_stop_hint(self):
        self.assertFalse(
            ep._should_infer_message_tool_continuation(
                "I'll wait here. Let me know if you need anything else."
            )
        )

    def test_infer_continuation_false_for_inline_chart_delivery(self):
        self.assertFalse(
            ep._should_infer_message_tool_continuation(
                "Let me send it here.\n\n<img src='$[/charts/signups_line.svg]'>"
            )
        )

    def test_infer_continuation_false_for_waiting_acknowledgement(self):
        self.assertFalse(
            ep._should_infer_message_tool_continuation(
                "Got it! I'll be right here when you need me."
            )
        )

    def test_infer_continuation_false_when_question_present(self):
        self.assertFalse(
            ep._should_infer_message_tool_continuation(
                "Can you share which story you want me to prioritize?"
            )
        )

    def test_infer_continuation_false_for_empty(self):
        self.assertFalse(ep._should_infer_message_tool_continuation(""))
        self.assertFalse(ep._should_infer_message_tool_continuation(None))
