import json
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.utils import timezone

from api.agent.files.filespace_service import write_bytes_to_dir
from api.evals.base import ScenarioTask
from api.evals.registry import register_scenario
from api.evals.scenarios.behavior_micro import (
    BehaviorMicroScenario,
    CharterMemoryScenario,
    _keeps_clauses,
    _uses_one_focused_charter_patch,
    get_tool_calls_for_run,
)
from api.evals.stop_policy import sqlite_batch_mutates_agent_config_field
from api.evals.tool_params import resolved_tool_param
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentCommsSnapshot,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentStepSnapshot,
)


GOOGLE_ANALYTICS_CREDENTIAL_RETENTION_SUITE_SLUG = "google_analytics_credential_retention"

GOOGLE_ANALYTICS_DIRECT_SETUP_PERSISTS_CHARTER = "google_analytics_direct_setup_persists_charter"
GOOGLE_ANALYTICS_DIRECT_CORRECTION_PERSISTS_CHARTER = (
    "google_analytics_direct_correction_persists_charter"
)
GOOGLE_ANALYTICS_DIRECT_ROUTE_SURVIVES_HISTORY_LOSS = (
    "google_analytics_direct_route_survives_history_loss"
)

GOOGLE_ANALYTICS_CREDENTIAL_RETENTION_SCENARIO_SLUGS = [
    GOOGLE_ANALYTICS_DIRECT_SETUP_PERSISTS_CHARTER,
    GOOGLE_ANALYTICS_DIRECT_CORRECTION_PERSISTS_CHARTER,
    GOOGLE_ANALYTICS_DIRECT_ROUTE_SURVIVES_HISTORY_LOSS,
]

GA4_PROPERTY_ID = "489999846"
GA4_SERVICE_ACCOUNT_PATH = "/Inbox/ga4-service-account.json"
GA4_PIPEDREAM_TOOL = "google_analytics-run-report-in-ga4"
DIRECT_EXECUTION_TOOLS = ("run_command", "python_exec")

GA4_EXISTING_CHARTER = (
    "Monitor organic search performance for gobii.ai through Google Analytics and Search Console. "
    "Alert the owner to meaningful traffic or ranking shifts and keep conclusions evidence-based."
)
GA4_DIRECT_AUTH_RULE = (
    "For Google Analytics, use direct service-account authentication rather than Pipedream: read the JSON key "
    f"from {GA4_SERVICE_ACCOUNT_PATH}, mint an OAuth bearer token with the analytics.readonly scope, and call the "
    f"Google Analytics Data API v1beta for property {GA4_PROPERTY_ID}. If a Pipedream Google Analytics tool asks "
    "for authorization, ignore that connection path and use this direct route. Never copy private-key contents "
    "into the charter or messages."
)
GA4_DIRECT_CHARTER = f"{GA4_EXISTING_CHARTER} {GA4_DIRECT_AUTH_RULE}"
GA4_DIRECT_SETUP_PROMPT = (
    f"I added the service-account JSON as $[{GA4_SERVICE_ACCOUNT_PATH}]. It should have access to the "
    "Google Analytics Data API now. Would you confirm it against the gobii.ai GA4 property? I'll add "
    "Search Console access separately."
)
GA4_REPORT_PROMPT = (
    "Compare Organic Search sessions for July 10-16 with July 20-26 in GA4 and tell me the "
    "percent change. Return only the verified comparison."
)

_EVAL_SERVICE_ACCOUNT_FIELDS = {
    "type": "service_account",
    "project_id": "eval-analytics-project",
    "private_key_id": "eval-private-key-id",
    "client_email": "seo-agent@eval-analytics-project.iam.gserviceaccount.com",
    "client_id": "123456789012345678901",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def _eval_service_account():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return {
        **_EVAL_SERVICE_ACCOUNT_FIELDS,
        "private_key": private_key_pem,
    }


_DIRECT_REPORT_RESULT = {
    "status": "ok",
    "exit_code": 0,
    "stdout": (
        "DIRECT_GA4_AUTH=service_account\n"
        f"PROPERTY_ID={GA4_PROPERTY_ID}\n"
        "COHORT_A_ORGANIC_SESSIONS=277\n"
        "COHORT_B_ORGANIC_SESSIONS=203\n"
        "PERCENT_CHANGE=-26.7\n"
    ),
}


def google_analytics_charter_blocks_direct_route(value):
    normalized = re.sub(r"\s+", " ", str(value or "").casefold())
    reconnect_gated = any(
        phrase in normalized
        for phrase in (
            "must reconnect pipedream",
            "need to reconnect pipedream",
            "requires pipedream",
            "use pipedream oauth",
            "only use the connected google analytics tool",
            "wait for pipedream authorization",
        )
    )
    claims_missing_direct_credentials = (
        any(term in normalized for term in ("service account", "service-account"))
        and any(
            phrase in normalized
            for phrase in (
                "is missing",
                "not configured",
                "not available",
                "must be provided",
                "needs to be provided",
            )
        )
    )
    return reconnect_gated or claims_missing_direct_credentials


def google_analytics_charter_has_reusable_direct_route(value):
    normalized = re.sub(r"\s+", " ", str(value or "").casefold())
    direct_over_pipedream = "pipedream" in normalized and (
        any(
            phrase in normalized
            for phrase in (
                "rather than pipedream",
                "instead of pipedream",
                "without pipedream",
                "no pipedream",
                "ignore that connection path",
                "do not use pipedream",
                "do not use the pipedream",
                "don't use pipedream",
                "never use pipedream",
                "never use the pipedream",
                "avoid pipedream",
                "preferred over pipedream",
                "takes precedence over pipedream",
                "takes precedence over any browser-based or pipedream",
            )
        )
        or bool(
            re.search(
                r"\b(?:do not|don't|never|avoid)\s+use\b.{0,100}\bpipedream\b",
                normalized,
            )
        )
        or bool(
            re.search(
                r"\bpipedream\b.{0,160}\b(?:do not|don't|never|avoid)\s+use\s+(?:(?:that|this|the)\s+route|it)\b",
                normalized,
            )
        )
        or bool(
            re.search(
                r"\b(?:preferred|takes precedence)\b.{0,160}\bpipedream\b",
                normalized,
            )
        )
    )
    has_credential_locator = (
        any(term in normalized for term in ("service account", "service-account"))
        and "json" in normalized
        and (
            GA4_SERVICE_ACCOUNT_PATH.casefold() in normalized
            or "ga4-service-account.json" in normalized
        )
    )
    has_auth_recipe = (
        "analytics.readonly" in normalized
        and any(term in normalized for term in ("oauth", "jwt", "bearer token"))
    )
    has_api_target = (
        GA4_PROPERTY_ID in normalized
        and any(
            term in normalized
            for term in (
                "analytics data api",
                "analyticsdata.googleapis.com",
                "google analytics data api",
                "ga4 api",
            )
        )
    )
    avoids_secret_copy = (
        "private" in normalized
        and "key" in normalized
        and any(term in normalized for term in ("never", "do not", "don't", "avoid"))
    )
    return (
        direct_over_pipedream
        and has_credential_locator
        and has_auth_recipe
        and has_api_target
        and avoids_secret_copy
        and not google_analytics_charter_blocks_direct_route(normalized)
    )


def _reply_does_not_choose_pipedream(value):
    normalized = re.sub(r"\s+", " ", str(value or "").casefold())
    return "pipedream" not in normalized or any(
        phrase in normalized
        for phrase in (
            "no pipedream",
            "without pipedream",
            "not pipedream",
            "did not use pipedream",
            "didn't use pipedream",
            "skipped pipedream",
        )
    )


def direct_google_analytics_execution_text(call):
    if call.tool_name == "run_command":
        return str(resolved_tool_param(call, "command") or "")
    if call.tool_name == "python_exec":
        return str(resolved_tool_param(call, "code") or "")
    return ""


def execution_uses_direct_google_analytics_route(call, *, require_property=False):
    text = direct_google_analytics_execution_text(call).casefold()
    if not text:
        return False
    has_service_account = (
        "service_account" in text
        or "service account" in text
        or "ga4-service-account.json" in text
    )
    has_google_analytics_api = any(
        term in text
        for term in (
            "analyticsdata.googleapis.com",
            "google.analytics.data",
            "analytics data api",
        )
    )
    has_auth = any(
        term in text
        for term in (
            "analytics.readonly",
            "oauth2.googleapis.com/token",
            "credentials.from_service_account",
            "jwt",
            "bearer",
        )
    )
    has_property = GA4_PROPERTY_ID in text
    return (
        has_service_account
        and has_google_analytics_api
        and has_auth
        and (has_property or not require_property)
    )


def _seed_service_account_file(agent):
    result = write_bytes_to_dir(
        agent,
        json.dumps(_eval_service_account()).encode("utf-8"),
        GA4_SERVICE_ACCOUNT_PATH,
        "application/json",
        extension=".json",
        overwrite=True,
    )
    if result.get("status") != "ok":
        raise ValueError(f"Failed to seed Google Analytics service-account fixture: {result}")


def _direct_ga_mock_config():
    valid_direct_rules = [
        {
            "param_contains": {
                param_name: [
                    "ga4-service-account.json",
                    "analytics.readonly",
                    api_marker,
                ],
            },
            "result": _DIRECT_REPORT_RESULT,
        }
        for param_name in ("command", "code")
        for api_marker in ("google.analytics.data", "analyticsdata.googleapis.com")
    ]
    invalid_direct_result = {
        "status": "error",
        "exit_code": 1,
        "stderr": (
            "ModuleNotFoundError: use the Google Analytics Data API client module "
            "or its analyticsdata.googleapis.com REST endpoint."
        ),
    }
    return {
        "run_command": {"rules": valid_direct_rules, "default": invalid_direct_result},
        "python_exec": {"rules": valid_direct_rules, "default": invalid_direct_result},
        GA4_PIPEDREAM_TOOL: {
            "status": "action_required",
            "result": "Authorization required. Connect the Pipedream Google Analytics OAuth account.",
            "connect_url": "https://example.test/connect/pipedream/google-analytics",
        },
    }


def _direct_setup_stop_policy():
    return {
        "ignore_sqlite_agent_config_mutations": False,
        "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
        "stop_on_tool_names": [
            GA4_PIPEDREAM_TOOL,
            "search_tools",
            "request_human_input",
            "secure_credentials_request",
        ],
        "stop_when_all_seen": [
            {
                "tool_name": "sqlite_batch",
                "agent_config_field": "charter",
                "after_execution": True,
            },
            {
                "tool_name": "run_command",
                "alternatives": ["python_exec"],
                "after_execution": True,
            },
            {"tool_name": "send_chat_message", "after_execution": True},
        ],
        "max_relevant_tool_calls": 10,
    }


class GoogleAnalyticsCharterScenario(CharterMemoryScenario):
    tags = ("agent_behavior", "micro", "charter", "memory", "google_analytics")

    def _seed_charter_agent(self, agent_id):
        super()._seed_charter_agent(agent_id)
        self._enable_sandbox_tool_visibility(agent_id)
        self._enable_builtin_tools(agent_id, ["read_file", *DIRECT_EXECUTION_TOOLS])
        self._enable_eval_synthetic_tools(agent_id, [GA4_PIPEDREAM_TOOL])
        _seed_service_account_file(PersistentAgent.objects.get(id=agent_id))

    def _inject_charter_prompt(self, run_id, agent_id):
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=_direct_ga_mock_config(),
                eval_stop_policy=self._eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )
        return inbound

    def _charter_check(self, agent, mutation_calls):
        preserved_role = _keeps_clauses(agent.charter, self.existing_charter)
        reusable_direct_route = google_analytics_charter_has_reusable_direct_route(agent.charter)
        focused_patch = _uses_one_focused_charter_patch(mutation_calls, self.existing_charter)
        passed = preserved_role and reusable_direct_route and focused_patch
        return (
            passed,
            (
                f"mutation_count={len(mutation_calls)}, focused_patch={focused_patch}, "
                f"preserved_role={preserved_role}, reusable_direct_route={reusable_direct_route}, "
                f"charter={agent.charter!r}."
            ),
        )


@register_scenario
class GoogleAnalyticsDirectSetupPersistsCharterScenario(GoogleAnalyticsCharterScenario):
    slug = GOOGLE_ANALYTICS_DIRECT_SETUP_PERSISTS_CHARTER
    description = (
        "A successful direct Google Analytics service-account setup should persist the reusable auth route."
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_direct_setup_and_charter", assertion_type="llm_judge"),
    ]
    existing_charter = GA4_EXISTING_CHARTER
    prompt = GA4_DIRECT_SETUP_PROMPT
    verification_task_name = "verify_direct_setup_and_charter"
    success_summary = "Agent verified direct GA4 access and saved a safe, reusable service-account route."
    failure_summary = (
        "Expected successful direct GA4 execution plus one focused charter patch with the reusable auth route"
    )
    semantic_judge_question = (
        "Does the updated charter preserve the organic-search monitoring role and safely record a reusable direct "
        "Google Analytics service-account workflow, including credential location, readonly OAuth/JWT token flow, "
        "Analytics Data API target/property, and an explicit preference over Pipedream without exposing key contents?"
    )

    def _eval_stop_policy(self):
        return _direct_setup_stop_policy()

    def _additional_charter_check(self, agent, run_id, inbound):
        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        direct_calls = [
            call
            for call in calls
            if call.tool_name in DIRECT_EXECUTION_TOOLS
            and str(call.status).casefold() == "complete"
            and execution_uses_direct_google_analytics_route(call)
        ]
        forbidden_calls = [
            call
            for call in calls
            if call.tool_name
            in {
                GA4_PIPEDREAM_TOOL,
                "search_tools",
                "request_human_input",
                "secure_credentials_request",
            }
        ]
        replies = [call for call in calls if call.tool_name == "send_chat_message"]
        reply_body = str(resolved_tool_param(replies[-1], "body") or "") if replies else ""
        reply_folded = reply_body.casefold()
        reported_success = (
            len(replies) == 1
            and str(replies[0].status).casefold() == "complete"
            and resolved_tool_param(replies[0], "will_continue_work") is False
            and any(term in reply_folded for term in ("confirmed", "verified", "works", "access"))
            and GA4_PROPERTY_ID in reply_body
            and _reply_does_not_choose_pipedream(reply_body)
            and "reconnect" not in reply_folded
        )
        passed = bool(direct_calls) and not forbidden_calls and reported_success
        return (
            passed,
            (
                f"direct_calls={len(direct_calls)}, "
                f"forbidden_tools={[call.tool_name for call in forbidden_calls]}, "
                f"reported_success={reported_success}, reply={reply_body!r}."
            ),
        )


@register_scenario
class GoogleAnalyticsDirectCorrectionPersistsCharterScenario(GoogleAnalyticsCharterScenario):
    slug = GOOGLE_ANALYTICS_DIRECT_CORRECTION_PERSISTS_CHARTER
    description = (
        "A correction away from a failed Pipedream GA4 route should persist the existing direct service-account path."
    )
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_direct_route_correction_saved", assertion_type="llm_judge"),
    ]
    existing_charter = GA4_EXISTING_CHARTER
    prior_outbound_body = (
        f"The GA4 property {GA4_PROPERTY_ID} is present, but every report returned Authorization required. "
        "This looks like an expired Pipedream OAuth connection, so please reconnect it before I continue."
    )
    prompt = (
        "Don't use Pipedream. I gave you the service-account key for direct "
        "Google Analytics Data API access with the analytics.readonly scope."
    )
    verification_task_name = "verify_direct_route_correction_saved"
    success_summary = (
        "Agent saved the direct GA4 service-account correction without requesting Pipedream reconnection."
    )
    failure_summary = "Expected one focused charter patch recording the direct GA4 route"
    verify_feedback_reply = True
    feedback_reply_options = {
        "allow_rejected_completions": True,
        "required_reply_concepts": (("direct", "service account", "key"),),
    }
    semantic_judge_question = (
        "Does the updated charter preserve the organic-search monitoring role and durably correct the auth workflow "
        "to use the existing service-account JSON with the direct Google Analytics Data API instead of Pipedream, "
        "without copying the private key into the charter? Does the reply naturally acknowledge that correction?"
    )

    def _eval_stop_policy(self):
        policy = super()._eval_stop_policy()
        policy["stop_on_tool_names"] = [
            GA4_PIPEDREAM_TOOL,
            "request_human_input",
            "secure_credentials_request",
        ]
        return policy


@register_scenario
class GoogleAnalyticsDirectRouteSurvivesHistoryLossScenario(BehaviorMicroScenario):
    slug = GOOGLE_ANALYTICS_DIRECT_ROUTE_SURVIVES_HISTORY_LOSS
    description = (
        "After a successful direct GA4 setup is persisted and its turn is compacted out of raw history, the actual "
        "charter written by the agent should still route work through service-account auth instead of Pipedream."
    )
    tags = ("agent_behavior", "micro", "charter", "memory", "google_analytics", "history_loss")
    existing_charter = GA4_EXISTING_CHARTER
    setup_prompt = GA4_DIRECT_SETUP_PROMPT
    report_prompt = GA4_REPORT_PROMPT
    tasks = [
        ScenarioTask(name="inject_setup_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_setup_charter_persisted", assertion_type="manual"),
        ScenarioTask(name="compact_setup_history", assertion_type="manual"),
        ScenarioTask(name="inject_report_request", assertion_type="manual"),
        ScenarioTask(name="verify_direct_route_used", assertion_type="manual"),
        ScenarioTask(name="verify_report_response", assertion_type="manual"),
        ScenarioTask(name="verify_charter_unchanged", assertion_type="manual"),
    ]

    def _prepare_agent(self, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        PersistentAgent.objects.filter(id=agent_id).update(charter=self.existing_charter)
        self._enable_sandbox_tool_visibility(agent_id)
        self._enable_builtin_tools(agent_id, ["sqlite_batch", "read_file", *DIRECT_EXECUTION_TOOLS])
        self._enable_eval_synthetic_tools(agent_id, [GA4_PIPEDREAM_TOOL])
        _seed_service_account_file(PersistentAgent.objects.get(id=agent_id))

    @staticmethod
    def _report_stop_policy(tool_calls_after=None):
        policy = {
            "ignore_sqlite_agent_config_mutations": False,
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
            "allowed_tool_names": [
                "sqlite_batch",
                "read_file",
                *DIRECT_EXECUTION_TOOLS,
                "send_chat_message",
            ],
            "stop_on_unexpected_relevant_tool": True,
            "stop_on_tool_names": [
                GA4_PIPEDREAM_TOOL,
                "search_tools",
                "request_human_input",
                "secure_credentials_request",
            ],
            "stop_when_all_seen": [
                {
                    "tool_name": "run_command",
                    "alternatives": ["python_exec"],
                    "after_execution": True,
                },
                {"tool_name": "send_chat_message", "after_execution": True},
            ],
            "max_relevant_tool_calls": 8,
        }
        if tool_calls_after:
            policy["tool_calls_after"] = tool_calls_after
        return policy

    @staticmethod
    def _compact_setup_history(agent_id):
        agent = PersistentAgent.objects.get(id=agent_id)
        latest_message = (
            PersistentAgentMessage.objects.filter(owner_agent=agent)
            .order_by("-timestamp")
            .first()
        )
        latest_step = (
            PersistentAgentStep.objects.filter(agent=agent)
            .order_by("-created_at")
            .first()
        )
        if latest_message is None or latest_step is None:
            raise ValueError("Cannot compact GA4 setup history without both messages and steps")

        PersistentAgentCommsSnapshot.objects.create(
            agent=agent,
            snapshot_until=latest_message.timestamp,
            summary=(
                "Earlier setup conversation completed successfully. "
                "Its implementation details are no longer available in message history."
            ),
        )
        PersistentAgentStepSnapshot.objects.create(
            agent=agent,
            snapshot_until=latest_step.created_at,
            summary=(
                "Earlier setup work completed successfully. "
                "Its tool details are no longer available in step history."
            ),
        )

    def run(self, run_id, agent_id):
        self._prepare_agent(agent_id)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_setup_prompt",
        )
        with self.wait_for_agent_idle(agent_id, timeout=120):
            setup_inbound = self.inject_message(
                agent_id,
                self.setup_prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=_direct_ga_mock_config(),
                eval_stop_policy=_direct_setup_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_setup_prompt",
            observed_summary="Direct GA4 setup prompt injected and processing completed.",
            artifacts={"message": setup_inbound},
        )

        setup_calls = get_tool_calls_for_run(run_id, after=setup_inbound.timestamp)
        setup_mutations = [
            call
            for call in setup_calls
            if sqlite_batch_mutates_agent_config_field(call, "charter")
        ]
        setup_direct_calls = [
            call
            for call in setup_calls
            if call.tool_name in DIRECT_EXECUTION_TOOLS
            and str(call.status).casefold() == "complete"
            and execution_uses_direct_google_analytics_route(call, require_property=True)
        ]
        setup_forbidden_calls = [
            call
            for call in setup_calls
            if call.tool_name
            in {
                GA4_PIPEDREAM_TOOL,
                "search_tools",
                "request_human_input",
                "secure_credentials_request",
            }
        ]
        setup_replies = [
            call
            for call in setup_calls
            if call.tool_name == "send_chat_message"
            and str(call.status).casefold() == "complete"
            and resolved_tool_param(call, "will_continue_work") is False
        ]
        agent = PersistentAgent.objects.get(id=agent_id)
        persisted_charter = agent.charter
        setup_passed = (
            _keeps_clauses(persisted_charter, self.existing_charter)
            and google_analytics_charter_has_reusable_direct_route(persisted_charter)
            and _uses_one_focused_charter_patch(setup_mutations, self.existing_charter)
            and bool(setup_direct_calls)
            and not setup_forbidden_calls
            and len(setup_replies) == 1
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if setup_passed else EvalRunTask.Status.FAILED,
            task_name="verify_setup_charter_persisted",
            observed_summary=(
                "Agent verified direct GA4 access and persisted its reusable route."
                if setup_passed
                else (
                    f"Expected successful direct setup and one reusable charter patch; "
                    f"mutations={len(setup_mutations)}, direct_calls={len(setup_direct_calls)}, "
                    f"forbidden_tools={[call.tool_name for call in setup_forbidden_calls]}, "
                    f"final_replies={len(setup_replies)}, charter={persisted_charter!r}."
                )
            ),
            artifacts={
                "step": (setup_mutations or setup_direct_calls or setup_forbidden_calls)[0].step
            }
            if setup_mutations or setup_direct_calls or setup_forbidden_calls
            else {},
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="compact_setup_history",
        )
        self._compact_setup_history(agent_id)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="compact_setup_history",
            observed_summary=(
                "Setup messages and steps were compacted behind summaries that omit the authentication route."
            ),
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="inject_report_request",
        )
        report_phase_started_at = timezone.now().isoformat()
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self.report_prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=_direct_ga_mock_config(),
                eval_stop_policy=self._report_stop_policy(
                    tool_calls_after=report_phase_started_at,
                ),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_report_request",
            observed_summary="GA4 report request injected with only the persisted charter carrying auth history.",
            artifacts={"message": inbound},
        )

        calls = get_tool_calls_for_run(run_id, after=inbound.timestamp)
        direct_calls = [
            call
            for call in calls
            if call.tool_name in DIRECT_EXECUTION_TOOLS
            and str(call.status).casefold() == "complete"
            and execution_uses_direct_google_analytics_route(call, require_property=True)
        ]
        forbidden_calls = [
            call
            for call in calls
            if call.tool_name
            in {
                GA4_PIPEDREAM_TOOL,
                "search_tools",
                "request_human_input",
                "secure_credentials_request",
            }
        ]
        direct_passed = bool(direct_calls) and not forbidden_calls
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if direct_passed else EvalRunTask.Status.FAILED,
            task_name="verify_direct_route_used",
            observed_summary=(
                "Agent used the chartered direct service-account route without touching Pipedream."
                if direct_passed
                else (
                    f"Expected a direct GA4 execution and no Pipedream/discovery/credential request; "
                    f"direct_calls={len(direct_calls)}, "
                    f"forbidden_tools={[call.tool_name for call in forbidden_calls]}."
                )
            ),
            artifacts={"step": (direct_calls or forbidden_calls)[0].step}
            if direct_calls or forbidden_calls
            else {},
        )

        replies = [call for call in calls if call.tool_name == "send_chat_message"]
        reply_body = str(resolved_tool_param(replies[-1], "body") or "") if replies else ""
        reply_folded = reply_body.casefold()
        response_passed = (
            len(replies) == 1
            and str(replies[0].status).casefold() == "complete"
            and resolved_tool_param(replies[0], "will_continue_work") is False
            and "277" in reply_body
            and "203" in reply_body
            and any(term in reply_folded for term in ("26.7", "27%", "27 percent"))
            and _reply_does_not_choose_pipedream(reply_body)
            and not any(
                term in reply_folded
                for term in ("reconnect", "authorization required", "cannot access")
            )
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if response_passed else EvalRunTask.Status.FAILED,
            task_name="verify_report_response",
            observed_summary=(
                "Agent returned the mocked verified cohort comparison."
                if response_passed
                else f"Expected the 277-to-203 direct GA4 comparison; reply={reply_body!r}."
            ),
            artifacts={"step": replies[-1].step} if replies else {},
        )

        agent = PersistentAgent.objects.get(id=agent_id)
        charter_mutations = [
            call
            for call in calls
            if sqlite_batch_mutates_agent_config_field(call, "charter")
        ]
        charter_passed = agent.charter == persisted_charter and not charter_mutations
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if charter_passed else EvalRunTask.Status.FAILED,
            task_name="verify_charter_unchanged",
            observed_summary=(
                "Persisted direct-auth charter remained intact."
                if charter_passed
                else (
                    f"Expected no charter mutation after using the persisted route; "
                    f"mutation_count={len(charter_mutations)}, charter={agent.charter!r}."
                )
            ),
            artifacts={"step": charter_mutations[0].step} if charter_mutations else {},
        )
