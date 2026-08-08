import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.prompt_context import (
    _discord_author_type,
    _get_managed_peer_first_run_instruction,
    _get_peer_communication_instruction,
)
from api.agent.tools.peer_dm import get_send_agent_message_tool
from api.agent.tools.send_discord_message import get_send_discord_message_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.responsibility_boundaries import (
    COORDINATOR_CHARTER,
    LEDGER_CHARTER,
    MANAGED_RESEARCH_CHARTER,
    REVIEWER_CHARTER,
    RESPONSIBILITY_BOUNDARY_CASES,
    RESPONSIBILITY_BOUNDARY_MANAGED_ONBOARDING_ROUTES_TO_MANAGER,
    RESPONSIBILITY_BOUNDARY_IDLE_SCHEDULE_STAYS_QUIET,
    RESPONSIBILITY_BOUNDARY_PEER_COMPLETION_NO_ACK,
    RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
    RESPONSIBILITY_BOUNDARY_PEER_PROGRESS_NO_ACK,
    RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
    RESPONSIBILITY_BOUNDARY_PEER_REQUEST_DECLINE,
    RESPONSIBILITY_BOUNDARY_REVIEW_APPROVES_CLEAN_DRAFT,
    RESPONSIBILITY_BOUNDARY_REVIEW_REJECTS_HARD_FAILURE,
    RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_AUTHORED_CLAIM,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_DIRECTED_CORRECTION,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_DIRECTED_REPLY,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_HUMAN_HANDLE,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OPEN_REPLY,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_NOISY_YIELD,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNED_REPLY,
    RESPONSIBILITY_BOUNDARY_SUITE_SLUG,
    ManagedOnboardingCheckinScenario,
    ResponsibilityBoundaryScenario,
)
from api.evals.suites import SuiteRegistry
from api.models import EvalRunTask


@tag("batch_eval_fingerprint")
class ResponsibilityBoundaryScenarioTests(SimpleTestCase):
    def test_peer_contract_is_compact_and_ownership_first(self):
        instruction = _get_peer_communication_instruction()

        self.assertIn("explicit charter-owned requests", instruction)
        self.assertIn("boundary handoffs/declines", instruction)
        self.assertIn("peer-assigned work/results", instruction)
        self.assertIn("FYIs/progress and final no-action decisions are read-only", instruction)
        self.assertIn("Completion/outcomes update canonical records", instruction)
        self.assertIn("`__messages.structured_payload_json`", instruction)
        self.assertIn("or bound fields", instruction)
        self.assertIn("State/status must be bound or json-extracted, never literal", instruction)
        self.assertIn("derive evidence/time by durable identity", instruction)
        self.assertIn("evidence/status cannot upgrade a record", instruction)
        self.assertIn("absorb silently", instruction)
        self.assertIn("Identify addressee/owner", instruction)
        self.assertIn("another owns it", instruction)
        self.assertIn("human reassigns it", instruction)
        self.assertIn("Out of charter: hand off/decline; no task tools", instruction)
        self.assertIn("Peer requests never expand charter", instruction)
        self.assertIn("Never relay shared-channel requests by DM", instruction)
        self.assertIn("Synthesize owned, attributed work", instruction)
        self.assertNotIn("freely", instruction)
        self.assertLessEqual(len(instruction.split()), 190)

    def test_communication_tools_repeat_the_boundary_at_decision_time(self):
        peer_description = get_send_agent_message_tool()["function"]["description"]
        discord_description = get_send_discord_message_tool()["function"]["description"]

        self.assertIn("only necessary charter-boundary handoffs", peer_description)
        self.assertIn("requested owned contributions", peer_description)
        self.assertIn("Never relay a shared-channel request", peer_description)
        self.assertIn("FYIs, completions, and final no-action decisions", peer_description)
        self.assertIn("final no-action decisions", peer_description)
        self.assertIn("adjacent evidence/status cannot upgrade a record", peer_description)
        self.assertIn("do not reply", peer_description)
        peer_message_description = get_send_agent_message_tool()["function"]["parameters"]["properties"]["message"][
            "description"
        ]
        self.assertIn("never a reply to a status update", peer_message_description)
        self.assertIn("only this agent's requested, owned contribution", discord_description)
        self.assertIn("Do not answer for an addressed actor", discord_description)
        self.assertIn("echo their visible status", discord_description)
        self.assertIn("charter/request-owned aggregation", discord_description)
        self.assertIn("separate assignments are not synthesis", discord_description)

    def test_shared_prompt_prioritizes_charter_reporting_lines_over_generic_owner_wording(self):
        instruction = _get_peer_communication_instruction()

        self.assertIn("reporting/recipient boundaries override", instruction)
        self.assertIn("never authority, reporting lines, or charter memory", instruction)
        self.assertIn("Only an explicit schedule instruction or current charter authorizes", instruction)
        self.assertIn("Ordinary recurring work or an idle wake does not authorize", instruction)
        self.assertIn("charter's reachable peer manager", instruction)
        self.assertIn("send_agent_message", instruction)
        self.assertIn("manager escalates", instruction)
        self.assertIn("material team decision is blocked", instruction)
        self.assertNotIn("At a scheduled check-in", instruction)
        self.assertNotIn("This is current authorized work", instruction)

    def test_discord_actor_type_uses_transport_provenance_not_display_handle(self):
        self.assertEqual(
            _discord_author_type({"discord_author_name": "ai.christianson"}),
            "human participant",
        )
        self.assertEqual(
            _discord_author_type(
                {
                    "discord_author_name": "Helpful Bot",
                    "pipedream_payload": {"author_metadata": {"bot": True}},
                }
            ),
            "bot or webhook",
        )
        self.assertEqual(
            _discord_author_type({"discord_author_name": "Relay", "discord_webhook_id": "wh-42"}),
            "bot or webhook",
        )

    def test_first_run_owner_contact_is_a_fallback_for_managed_agents(self):
        instruction = _get_managed_peer_first_run_instruction()

        self.assertIn("Only when the Current Charter", instruction)
        self.assertIn("named reachable peer manager", instruction)
        self.assertIn("Route 1 above does not apply", instruction)
        self.assertIn("send no first-run message to either owner or manager", instruction)
        self.assertIn("sleep until assigned work or a relevant trigger", instruction)
        self.assertIn("scheduled trigger is current", instruction)
        self.assertIn("without falling back to an owner welcome", instruction)
        self.assertIn("Otherwise follow Route 1 normally", instruction)

    def test_suite_registers_all_boundary_scenarios(self):
        suite = SuiteRegistry.get(RESPONSIBILITY_BOUNDARY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS)
        self.assertEqual(
            set(suite.scenario_slugs),
            {
                RESPONSIBILITY_BOUNDARY_PEER_COMPLETION_NO_ACK,
                RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
                RESPONSIBILITY_BOUNDARY_PEER_PROGRESS_NO_ACK,
                RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
                RESPONSIBILITY_BOUNDARY_PEER_REQUEST_DECLINE,
                RESPONSIBILITY_BOUNDARY_REVIEW_APPROVES_CLEAN_DRAFT,
                RESPONSIBILITY_BOUNDARY_REVIEW_REJECTS_HARD_FAILURE,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNED_REPLY,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_NOISY_YIELD,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_AUTHORED_CLAIM,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_DIRECTED_CORRECTION,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_DIRECTED_REPLY,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OPEN_REPLY,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_HUMAN_HANDLE,
                RESPONSIBILITY_BOUNDARY_MANAGED_ONBOARDING_ROUTES_TO_MANAGER,
                RESPONSIBILITY_BOUNDARY_IDLE_SCHEDULE_STAYS_QUIET,
            },
        )

    def test_scenarios_use_the_real_harness_and_low_cost_metadata(self):
        for slug in RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS:
            scenario = ScenarioRegistry.get(slug)
            metadata = scenario.get_metadata()

            self.assertEqual(metadata.category, "responsibility_boundaries")
            self.assertEqual(metadata.area, "agent_behavior")
            self.assertEqual(metadata.expected_runtime, "short")
            self.assertEqual(metadata.cost_class, "low")
            self.assertIn("real_harness", metadata.tags)

    def test_sqlite_batch_counts_as_a_substantive_action(self):
        sqlite_call = SimpleNamespace(tool_name="sqlite_batch")

        self.assertEqual(ResponsibilityBoundaryScenario._action_calls([sqlite_call]), [sqlite_call])

    def test_events_do_not_state_the_expected_behavior(self):
        prompts = " ".join(case.prompt for case in RESPONSIBILITY_BOUNDARY_CASES).lower()

        self.assertNotIn("stay in your lane", prompts)
        self.assertNotIn("do not acknowledge this", prompts)
        self.assertNotIn("do not answer this", prompts)
        self.assertNotIn("shared channels", LEDGER_CHARTER)
        self.assertNotIn("stay in your lane", COORDINATOR_CHARTER.lower())
        self.assertIn("customer-signal curation and reporting", LEDGER_CHARTER)
        self.assertIn("every hard rule", REVIEWER_CHARTER)

    def test_managed_onboarding_fixture_preserves_the_generalized_responsibility_boundary(self):
        charter = MANAGED_RESEARCH_CHARTER.lower()

        self.assertIn("prospect researcher", charter)
        self.assertIn("morgan lee, your manager", charter)
        self.assertIn("research packet", charter)
        self.assertIn("do not source unassigned companies", charter)
        self.assertIn("do not", charter)
        self.assertIn("day-to-day cadence", charter)
        self.assertIn("peer channel", charter)
        self.assertIn("material decision cannot be resolved within the team", charter)
        self.assertNotIn("onboarding", charter)
        self.assertNotIn("first check-in", charter)
        self.assertNotIn("when the schedule fires", charter)

    def test_managed_onboarding_intercepts_owner_channels_but_keeps_peer_messaging_real(self):
        mock_config = ManagedOnboardingCheckinScenario._mock_config()

        self.assertEqual(
            set(mock_config),
            {
                "send_email",
                "send_sms",
                "send_chat_message",
                "request_contact_permission",
            },
        )
        self.assertNotIn("send_agent_message", mock_config)
        self.assertTrue(all(result["status"] == "error" for result in mock_config.values()))

    def test_managed_onboarding_manager_route_scoring_is_deterministic(self):
        manager_id = "97d31504-0000-4000-8000-000000000001"
        successful_manager_call = SimpleNamespace(
            tool_name="send_agent_message",
            tool_params={"peer_agent_id": manager_id, "message": "Should we adjust the current cadence?"},
            status="complete",
            result=json.dumps({"status": "success"}),
        )

        self.assertEqual(
            ManagedOnboardingCheckinScenario._manager_route_failures(
                [successful_manager_call],
                manager_id,
            ),
            [],
        )

        owner_email_call = SimpleNamespace(tool_name="send_email")
        failures = ManagedOnboardingCheckinScenario._manager_route_failures(
            [successful_manager_call, owner_email_call],
            manager_id,
        )
        self.assertTrue(any("owner-facing contact" in failure for failure in failures))

        wrong_manager_call = SimpleNamespace(
            tool_name="send_agent_message",
            tool_params={"peer_agent_id": "97d31504-0000-4000-8000-000000000002"},
            status="complete",
            result=json.dumps({"status": "success"}),
        )
        failures = ManagedOnboardingCheckinScenario._manager_route_failures(
            [wrong_manager_call],
            manager_id,
        )
        self.assertTrue(any("instead of the manager" in failure for failure in failures))

    def test_managed_onboarding_detects_charter_and_schedule_mutations(self):
        charter_mutation = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={
                "sql": (
                    "UPDATE __agent_config SET charter = "
                    "'Check in with the owner on every run' WHERE id = 1"
                )
            },
        )
        schedule_mutation = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={
                "sql": (
                    "UPDATE __agent_schedules SET enabled = 0 "
                    "WHERE schedule_key = 'onboarding_checkin'"
                )
            },
        )
        read_only = SimpleNamespace(
            tool_name="sqlite_batch",
            tool_params={"sql": "SELECT charter FROM __agent_config WHERE id = 1"},
        )

        self.assertEqual(
            ManagedOnboardingCheckinScenario._config_mutation_calls(
                [read_only, charter_mutation, schedule_mutation]
            ),
            [charter_mutation, schedule_mutation],
        )

    def test_directed_reply_case_relies_on_discord_reply_metadata(self):
        case = next(
            case for case in RESPONSIBILITY_BOUNDARY_CASES
            if case.event_kind == "shared_channel_directed_reply"
        )

        self.assertEqual(case.slug, RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_DIRECTED_REPLY)
        self.assertNotIn("engineering agent", case.prompt.lower())
        self.assertNotIn("customer signals", case.prompt.lower())
        self.assertIn("you", case.prompt.lower())

    def test_open_reply_case_invites_owned_help_without_naming_the_agent(self):
        case = next(
            case for case in RESPONSIBILITY_BOUNDARY_CASES
            if case.event_kind == "shared_channel_open_reply"
        )

        self.assertEqual(case.slug, RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OPEN_REPLY)
        self.assertNotIn("customer signals agent", case.prompt.lower())
        self.assertIn("anyone with confirmed customer context", case.prompt.lower())

    def test_noisy_shared_channel_allows_silent_sqlite_tracking(self):
        case = next(case for case in RESPONSIBILITY_BOUNDARY_CASES if case.event_kind == "shared_channel_noisy")
        scenario = ResponsibilityBoundaryScenario(case)
        recorded = []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))

        scenario._verify_no_interference(
            "run",
            [SimpleNamespace(tool_name="sqlite_batch")],
            allowed={"sqlite_batch"},
        )
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

        scenario._verify_no_interference(
            "run",
            [SimpleNamespace(tool_name="send_discord_message", step="discord-step")],
            allowed={"sqlite_batch"},
        )
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.FAILED)

    def test_authored_claim_case_addresses_the_other_agent_and_forbids_answering(self):
        case = next(
            case for case in RESPONSIBILITY_BOUNDARY_CASES if case.event_kind == "shared_channel_authored_claim"
        )
        self.assertEqual(case.slug, RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_AUTHORED_CLAIM)
        # The verification request names the engineering peer, never the agent under test.
        self.assertIn("engineering agent", case.prompt.lower())
        self.assertNotIn("customer signals", case.prompt.lower())

        scenario = ResponsibilityBoundaryScenario(case)
        recorded = []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))

        # Staying out of it passes, and so does reacting: chiming in is not the failure.
        scenario._verify_yields_verification("run", [])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

        scenario._verify_yields_verification("run", [SimpleNamespace(tool_name="add_discord_reaction", step="s")])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

        # Relaying the addressed request by DM is always a failure, with no judge call needed.
        scenario._verify_yields_verification(
            "run",
            [SimpleNamespace(tool_name="send_agent_message", step="dm-step")],
        )
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.FAILED)

    def test_authored_claim_judges_a_channel_reply_instead_of_banning_it(self):
        case = next(
            case for case in RESPONSIBILITY_BOUNDARY_CASES if case.event_kind == "shared_channel_authored_claim"
        )
        scenario = ResponsibilityBoundaryScenario(case)
        recorded = []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        channel_call = SimpleNamespace(
            tool_name="send_discord_message",
            tool_params={"message": "Adding the raw report IDs in case they help."},
            step="discord-step",
        )

        scenario.llm_judge = lambda **kwargs: ("Yields", "Adds owned detail without a verdict.")
        scenario._verify_yields_verification("run", [channel_call])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

        scenario.llm_judge = lambda **kwargs: ("Answers in their place", "Confirms its own finding.")
        scenario._verify_yields_verification("run", [channel_call])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.FAILED)

    def test_owned_reply_accepts_boundary_disclaimer_but_rejects_takeover(self):
        case = next(case for case in RESPONSIBILITY_BOUNDARY_CASES if case.event_kind == "shared_channel_owned")
        scenario = ResponsibilityBoundaryScenario(case)
        recorded = []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        inbound = SimpleNamespace(raw_payload={
            "discord_guild_id": "guild-1",
            "discord_channel_name": "team-chat",
        })
        http_call = SimpleNamespace(
            tool_name="http_request",
            tool_params={"url": "https://api.example.test/customer-signals-summary.json"},
            status="complete",
            result=json.dumps({"status": "success"}),
            step="http-step",
        )
        discord_call = SimpleNamespace(
            tool_name="send_discord_message",
            tool_params={
                "guild_id": "guild-1",
                "channel_name": "team-chat",
                "message": "Top confirmed theme: Export reliability, with three confirmed reports.",
                "will_continue_work": False,
            },
            status="complete",
            result=json.dumps({"status": "success"}),
            step="discord-step",
        )
        sqlite_call = SimpleNamespace(tool_name="sqlite_batch")

        scenario._verify_owned_request("run", inbound, [http_call, sqlite_call, discord_call])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

        discord_call.tool_params["message"] += " Engineering is checking the empty-CSV root cause."
        scenario._verify_owned_request("run", inbound, [http_call, discord_call])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

        discord_call.tool_params["message"] += " I'll investigate that too."
        scenario._verify_owned_request("run", inbound, [http_call, discord_call])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.FAILED)

    def test_owned_reply_accepts_scrape_fetch_for_opaque_json_link(self):
        case = next(case for case in RESPONSIBILITY_BOUNDARY_CASES if case.event_kind == "shared_channel_owned")
        scenario = ResponsibilityBoundaryScenario(case)
        recorded = []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        inbound = SimpleNamespace(raw_payload={
            "discord_guild_id": "guild-1",
            "discord_channel_name": "team-chat",
        })
        scrape_call = SimpleNamespace(
            tool_name="mcp_brightdata_scrape_as_markdown",
            tool_params={"url": "https://api.example.test/customer-signals-summary.json"},
            status="complete",
            result=json.dumps({"status": "success"}),
            step="scrape-step",
        )
        discord_call = SimpleNamespace(
            tool_name="send_discord_message",
            tool_params={
                "guild_id": "guild-1",
                "channel_name": "team-chat",
                "message": "Top confirmed theme: Export reliability, with three confirmed reports.",
                "will_continue_work": False,
            },
            status="complete",
            result=json.dumps({"status": "success"}),
            step="discord-step",
        )

        scenario._verify_owned_request("run", inbound, [scrape_call, discord_call])

        self.assertEqual(recorded[-2][0][2], EvalRunTask.Status.PASSED)
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

    @patch("api.evals.scenarios.responsibility_boundaries.PersistentAgentEnabledTool.objects")
    @patch("api.evals.scenarios.responsibility_boundaries.PersistentAgent.objects")
    @patch.object(ResponsibilityBoundaryScenario, "_seed_prior_run")
    @patch("api.evals.scenarios.responsibility_boundaries.mark_tool_enabled_without_discovery")
    def test_prepare_agent_exposes_both_owned_request_fetch_tools(
        self,
        mark_enabled,
        _seed_prior_run,
        agent_objects,
        enabled_tool_objects,
    ):
        case = next(case for case in RESPONSIBILITY_BOUNDARY_CASES if case.event_kind == "shared_channel_owned")
        scenario = ResponsibilityBoundaryScenario(case)
        agent = MagicMock()
        agent_objects.select_related.return_value.get.return_value = agent

        scenario._prepare_agent("agent-1")

        self.assertEqual(
            mark_enabled.call_args_list,
            [
                call(agent, "http_request"),
                call(agent, "mcp_brightdata_scrape_as_markdown"),
            ],
        )
        enabled_tool_objects.filter.assert_called_once_with(
            agent=agent,
            tool_full_name="mcp_brightdata_scrape_as_markdown",
        )
        enabled_tool_objects.filter.return_value.update.assert_called_once_with(
            tool_server="eval",
            tool_name="mcp_brightdata_scrape_as_markdown",
        )
