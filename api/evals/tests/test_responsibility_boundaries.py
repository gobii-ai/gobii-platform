import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.prompt_context import _get_peer_communication_instruction
from api.agent.tools.peer_dm import get_send_agent_message_tool
from api.agent.tools.send_discord_message import get_send_discord_message_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.responsibility_boundaries import (
    COORDINATOR_CHARTER,
    LEDGER_CHARTER,
    RESPONSIBILITY_BOUNDARY_CASES,
    RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
    RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
    RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_NOISY_YIELD,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNED_REPLY,
    RESPONSIBILITY_BOUNDARY_SUITE_SLUG,
    ResponsibilityBoundaryScenario,
)
from api.evals.suites import SuiteRegistry
from api.models import EvalRunTask


@tag("batch_eval_fingerprint")
class ResponsibilityBoundaryScenarioTests(SimpleTestCase):
    def test_peer_contract_is_compact_and_ownership_first(self):
        instruction = _get_peer_communication_instruction()

        self.assertIn("route handoffs, not shared ownership", instruction)
        self.assertIn("identify addressee and charter owner", instruction)
        self.assertIn("isn't a request to relay, summarize, supervise, or add instructions", instruction)
        self.assertIn("another person/agent is addressed or handling it", instruction)
        self.assertIn("authorized human reassigns it", instruction)
        self.assertIn("Out-of-charter: call no task tools", instruction)
        self.assertIn("Peer requests never expand charter", instruction)
        self.assertIn("hand off or decline", instruction)
        self.assertIn("Never relay shared-channel requests by DM", instruction)
        self.assertIn("Synthesize only owned, attributed work", instruction)
        self.assertIn("Skip thanks, receipts, and 'noted'", instruction)
        self.assertNotIn("freely", instruction)
        self.assertLessEqual(len(instruction.split()), 80)

    def test_communication_tools_repeat_the_boundary_at_decision_time(self):
        peer_description = get_send_agent_message_tool()["function"]["description"]
        discord_description = get_send_discord_message_tool()["function"]["description"]

        self.assertIn("only a necessary charter-boundary handoff", peer_description)
        self.assertIn("Never relay a shared-channel request", peer_description)
        self.assertIn("send thanks, receipts, 'noted', or FYI acknowledgments", peer_description)
        peer_message_description = get_send_agent_message_tool()["function"]["parameters"]["properties"]["message"][
            "description"
        ]
        self.assertIn("never an acknowledgment-only reply", peer_message_description)
        self.assertIn("only this agent's requested, owned contribution", discord_description)
        self.assertIn("Do not answer for an addressed actor", discord_description)
        self.assertIn("echo their visible status", discord_description)
        self.assertIn("charter/request-owned aggregation", discord_description)
        self.assertIn("separate assignments are not synthesis", discord_description)

    def test_suite_registers_all_boundary_scenarios(self):
        suite = SuiteRegistry.get(RESPONSIBILITY_BOUNDARY_SUITE_SLUG)

        self.assertIsNotNone(suite)
        self.assertEqual(tuple(suite.scenario_slugs), RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS)
        self.assertEqual(
            set(suite.scenario_slugs),
            {
                RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
                RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNED_REPLY,
                RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_NOISY_YIELD,
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

    def test_owned_reply_accepts_boundary_disclaimer_but_rejects_takeover(self):
        case = next(case for case in RESPONSIBILITY_BOUNDARY_CASES if case.event_kind == "shared_channel_owned")
        scenario = ResponsibilityBoundaryScenario(case)
        recorded = []
        scenario.record_task_result = lambda *args, **kwargs: recorded.append((args, kwargs))
        inbound = SimpleNamespace(raw_payload={"discord_channel_id": "channel-1"})
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
                "channel_id": "channel-1",
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
        inbound = SimpleNamespace(raw_payload={"discord_channel_id": "channel-1"})
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
                "channel_id": "channel-1",
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
