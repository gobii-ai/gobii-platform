import json
from types import SimpleNamespace

from django.test import SimpleTestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.agent.core.prompt_context import _get_peer_communication_instruction
from api.agent.tools.peer_dm import get_send_agent_message_tool
from api.agent.tools.send_discord_message import get_send_discord_message_tool
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.responsibility_boundaries import (
    LEDGER_CHARTER,
    RESPONSIBILITY_BOUNDARY_CASES,
    RESPONSIBILITY_BOUNDARY_PEER_FYI_NO_ACK,
    RESPONSIBILITY_BOUNDARY_PEER_REQUEST_HANDOFF,
    RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS,
    RESPONSIBILITY_BOUNDARY_SHARED_CHANNEL_OWNER,
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
        self.assertIn("Before any task tool, check ownership", instruction)
        self.assertIn("For out-of-charter work, call no task tools", instruction)
        self.assertIn("Peer requests never expand charter", instruction)
        self.assertIn("hand off or decline", instruction)
        self.assertIn("your charter owns it", instruction)
        self.assertIn("report only that slice", instruction)
        self.assertIn("omit parallel assignments", instruction)
        self.assertIn("never relay by peer DM", instruction)
        self.assertIn("synthesize others' work only when owned and attributed", instruction)
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
        self.assertIn("this agent's requested, owned contribution", discord_description)
        self.assertIn("charter or request owns the aggregation", discord_description)
        self.assertIn("attribute it", discord_description)
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
        self.assertIn("customer-signal curation and reporting", LEDGER_CHARTER)

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

        scenario._verify_owned_request("run", inbound, [http_call, discord_call])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

        discord_call.tool_params["message"] += " Engineering is checking the empty-CSV root cause."
        scenario._verify_owned_request("run", inbound, [http_call, discord_call])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.PASSED)

        discord_call.tool_params["message"] += " I'll investigate that too."
        scenario._verify_owned_request("run", inbound, [http_call, discord_call])
        self.assertEqual(recorded[-1][0][2], EvalRunTask.Status.FAILED)
