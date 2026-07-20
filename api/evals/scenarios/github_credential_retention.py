import json
from unittest.mock import patch

from api.agent.core.agent_judge import build_manual_judge_trigger, run_manual_agent_judge
from api.agent.core.llm_config import get_agent_judge_llm_config
from api.agent.tools.run_command import get_run_command_tool
from api.evals.base import ScenarioTask
from api.evals.execution import get_current_eval_routing_profile
from api.evals.registry import register_scenario
from api.evals.scenarios.behavior_micro import (
    CHARTER_JUDGE_PRESERVES_CLI_GITHUB_SECRET_WORKFLOW,
    CHARTER_RECORDS_CLI_GITHUB_SECRETS_CORRECTION,
    CharterMemoryScenario,
    get_tool_calls_for_run,
)
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentSecret,
    PersistentAgentStep,
    PersistentAgentToolCall,
)


GITHUB_APP_ENV_SECRET_FIXTURES = (
    {
        "name": "GitHub App ID",
        "key": "GITHUB_APP_ID",
        "description": "Numeric App ID for the GitHub App installed on the repository.",
        "value": "EVAL_GITHUB_APP_ID_VALUE_123456",
    },
    {
        "name": "GitHub App Private Key",
        "key": "GITHUB_APP_PRIVATE_KEY",
        "description": "Private key used to mint short-lived GitHub App installation tokens.",
        "value": "-----BEGIN RSA PRIVATE KEY-----\nEVAL_GITHUB_PRIVATE_KEY_VALUE\n-----END RSA PRIVATE KEY-----",
    },
    {
        "name": "GitHub App Installation ID",
        "key": "GITHUB_APP_INSTALLATION_ID",
        "description": "Installation ID for the GitHub App on the repository.",
        "value": "EVAL_GITHUB_INSTALLATION_ID_VALUE_654321",
    },
)
GITHUB_APP_ENV_KEYS = tuple(fixture["key"] for fixture in GITHUB_APP_ENV_SECRET_FIXTURES)
GITHUB_DOCS_EXISTING_CHARTER = (
    "Write user-friendly product documentation by reviewing the application and source code. "
    "For each change, create a dedicated branch, commit, push, and open a pull request. "
    "Assign each pull request to Matt as reviewer and assignee. Use command-line git for all GitHub work. "
    "Never merge without the user's explicit approval."
)
GITHUB_DOCS_CORRECTED_CHARTER = (
    f"{GITHUB_DOCS_EXISTING_CHARTER} "
    "Authenticate command-line GitHub work with the configured GitHub App environment-variable secrets; "
    "a disconnected native GitHub integration does not remove that authorized CLI access path."
)


def seed_github_app_env_var_secrets(agent_id):
    secrets = []
    for fixture in GITHUB_APP_ENV_SECRET_FIXTURES:
        secret = PersistentAgentSecret(
            agent_id=agent_id,
            name=fixture["name"],
            description=fixture["description"],
            key=fixture["key"],
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
            domain_pattern=PersistentAgentSecret.ENV_VAR_DOMAIN_SENTINEL,
            requested=False,
        )
        secret.set_value(fixture["value"])
        secret.save()
        secrets.append(secret)
    return secrets


def sanitized_github_secret_metadata(agent_id):
    secrets_by_key = {
        secret.key: secret
        for secret in PersistentAgentSecret.objects.filter(
            agent_id=agent_id,
            key__in=GITHUB_APP_ENV_KEYS,
            secret_type=PersistentAgentSecret.SecretType.ENV_VAR,
        )
    }
    return [
        {
            "name": secrets_by_key[key].name,
            "key": key,
            "secret_type": secrets_by_key[key].secret_type,
            "status": "pending" if secrets_by_key[key].requested else "available",
        }
        for key in GITHUB_APP_ENV_KEYS
        if key in secrets_by_key
    ]


def judge_github_secret_context_check(trajectory):
    current_context = trajectory.get("current_context") or {}
    serialized_context = json.dumps(current_context, sort_keys=True, default=str)
    missing_keys = [key for key in GITHUB_APP_ENV_KEYS if key not in serialized_context]
    leaked_fixture_values = [
        fixture["key"]
        for fixture in GITHUB_APP_ENV_SECRET_FIXTURES
        if fixture["value"] in serialized_context
    ]
    passed = not missing_keys and not leaked_fixture_values
    detail = (
        f"missing_keys={missing_keys}, leaked_fixture_values={leaked_fixture_values}, "
        f"current_context_keys={sorted(current_context)}"
    )
    return passed, detail


def github_guidance_blocks_configured_cli_path(text):
    normalized = str(text or "").casefold()
    blocks_local_path = any(
        phrase in normalized
        for phrase in (
            "do not use local",
            "don't use local",
            "stop using local",
            "do not use cli",
            "don't use cli",
            "stop using cli",
            "do not use shell",
            "stop using shell",
        )
    )
    reconnect_gated = (
        ("reconnect" in normalized or "authoriz" in normalized)
        and any(
            phrase in normalized
            for phrase in (
                "must reconnect",
                "needs reconnect",
                "need to reconnect",
                "until reconnect",
                "until github is reconnect",
                "wait for",
                "only after",
                "send one reconnect request",
                "immediately send the connection",
            )
        )
    )
    claims_missing_credentials = (
        any(term in normalized for term in ("secret", "credential"))
        and any(term in normalized for term in ("is missing", "are missing", "not configured", "provide the"))
    )
    return blocks_local_path or reconnect_gated or claims_missing_credentials


def judge_guidance_preserves_cli_github_path(result):
    if not result.get("ran"):
        return False, f"Judge did not run: status={result.get('status')!r}."

    suggestion_type = str(result.get("suggestion_type") or "").casefold()
    suggestion = result.get("suggestion") or {}
    if suggestion_type == "no_action" and not suggestion:
        return True, "Judge returned no_action."
    if not suggestion:
        return False, f"Judge returned {suggestion_type or 'unknown'} without a suggestion payload."

    guidance = " ".join(str(suggestion.get(key) or "") for key in ("message", "agentDirective")).strip()
    if github_guidance_blocks_configured_cli_path(guidance):
        return False, f"Judge guidance blocks the configured CLI path: {guidance!r}."

    normalized = guidance.casefold()
    addresses_github_auth = any(
        term in normalized
        for term in ("github", "auth", "token", "secret", "credential", "reconnect")
    )
    preserves_cli_path = any(
        term in normalized
        for term in (
            "run_command",
            "command line",
            "cli",
            "environment variable",
            "env var",
            "github app",
        )
    )
    if addresses_github_auth and not preserves_cli_path:
        return False, f"GitHub/auth guidance omitted the configured CLI/env-var path: {guidance!r}."
    return True, "Judge returned no harmful reconnect-only or CLI-blocking guidance."


@register_scenario
class CharterRecordsCliGithubSecretsCorrectionScenario(CharterMemoryScenario):
    slug = CHARTER_RECORDS_CLI_GITHUB_SECRETS_CORRECTION
    description = "A correction about configured GitHub App secrets should become durable CLI workflow guidance."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_cli_secret_workflow_saved", assertion_type="manual"),
    ]
    existing_charter = GITHUB_DOCS_EXISTING_CHARTER
    prior_outbound_body = (
        "One blocker: the GitHub integration is still disconnected, so I cannot push a pull request. "
        "Please reconnect GitHub before I continue."
    )
    prompt = "you should have github secrets that allow you to use github."
    verification_task_name = "verify_cli_secret_workflow_saved"
    success_summary = "Agent saved the configured GitHub App secret route without weakening the existing PR workflow."
    failure_summary = "Expected one targeted charter patch preserving the docs workflow and recording CLI secret access"

    def _eval_stop_policy(self):
        policy = super()._eval_stop_policy()
        policy["stop_on_tool_names"] = ["request_human_input", "secure_credentials_request"]
        return policy

    def _seed_charter_agent(self, agent_id):
        super()._seed_charter_agent(agent_id)
        seed_github_app_env_var_secrets(agent_id)

    def _charter_check(self, agent, mutation_calls):
        charter = (agent.charter or "").casefold()
        sql = "\n".join(json.dumps(call.tool_params or {}) for call in mutation_calls).casefold()
        preserved_workflow = all(
            term in charter
            for term in (
                "documentation",
                "branch",
                "pull request",
                "reviewer",
                "assignee",
                "never merge",
                "command-line git",
            )
        )
        records_secret_route = (
            "github app" in charter
            and any(term in charter for term in ("secret", "credential", "environment"))
            and any(term in charter for term in ("cli", "command-line", "command line", "git"))
        )
        used_one_patch = len(mutation_calls) == 1 and "patch_text" in sql
        blocks_valid_path = github_guidance_blocks_configured_cli_path(agent.charter)
        passed = used_one_patch and preserved_workflow and records_secret_route and not blocks_valid_path
        return (
            passed,
            (
                f"mutation_count={len(mutation_calls)}, used_patch={used_one_patch}, "
                f"preserved_workflow={preserved_workflow}, records_secret_route={records_secret_route}, "
                f"blocks_valid_path={blocks_valid_path}, charter={agent.charter!r}."
            ),
        )

    def _additional_charter_check(self, agent, run_id, inbound):
        credential_request_calls = get_tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            tool_names=["request_human_input", "secure_credentials_request"],
        )
        pending_github_secrets = PersistentAgentSecret.objects.filter(
            agent=agent,
            key__in=GITHUB_APP_ENV_KEYS,
            requested=True,
        ).count()
        passed = not credential_request_calls and pending_github_secrets == 0
        return (
            passed,
            (
                f"credential_request_tools={[call.tool_name for call in credential_request_calls]}, "
                f"pending_github_secrets={pending_github_secrets}."
            ),
        )


@register_scenario
class CharterJudgePreservesCliGithubSecretWorkflowScenario(CharterMemoryScenario):
    slug = CHARTER_JUDGE_PRESERVES_CLI_GITHUB_SECRET_WORKFLOW
    description = "The trajectory judge should not replace a user-authorized CLI secret path with reconnect-only guidance."
    tasks = [
        ScenarioTask(name="seed_judge_trajectory", assertion_type="manual"),
        ScenarioTask(name="verify_judge_secret_capability_context", assertion_type="manual"),
        ScenarioTask(name="run_manual_judge", assertion_type="manual"),
        ScenarioTask(name="verify_judge_guidance_preserved_cli", assertion_type="manual"),
    ]
    existing_charter = GITHUB_DOCS_CORRECTED_CHARTER

    @staticmethod
    def _seed_tool_call(agent_id, run_id, tool_name, tool_params, result):
        step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            eval_run_id=run_id,
            description=f"Seeded GitHub credential-retention trajectory: {tool_name}",
        )
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name=tool_name,
            tool_params=tool_params,
            result=json.dumps(result),
            status="complete",
        )
        return step

    @staticmethod
    def _sanitized_judge_result(result):
        suggestion = result.get("suggestion") or {}
        return {
            "ran": bool(result.get("ran")),
            "status": result.get("status"),
            "suggestion_type": result.get("suggestion_type"),
            "completion_id": result.get("completion_id"),
            "suggestion": {
                "message": suggestion.get("message"),
                "agentDirective": suggestion.get("agentDirective"),
                "status": suggestion.get("status"),
            } if suggestion else None,
        }

    def _seed_judge_trajectory(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        PersistentAgent.objects.filter(id=agent_id).update(charter=self.existing_charter)
        self._seed_prior_processing_run(agent_id)
        self._enable_builtin_tools(agent_id, ["run_command"])
        seed_github_app_env_var_secrets(agent_id)
        self._seed_prior_outbound_message(
            agent_id,
            "The GitHub integration is disconnected, so I cannot continue until you reconnect it.",
        )
        self.inject_message(
            agent_id,
            "you should have github secrets that allow you to use github.",
            trigger_processing=False,
            eval_run_id=run_id,
        )
        self._seed_tool_call(
            agent_id,
            run_id,
            "github-get-pull-request",
            {"repoFullname": "example/docs", "pullNumber": 241},
            {
                "status": "action_required",
                "result": "Authorization required. Please connect your account.",
                "connect_url": "https://example.test/connect/github",
            },
        )
        self._seed_tool_call(
            agent_id,
            run_id,
            "python_exec",
            {"code": "Check whether the configured GitHub App environment variables are present."},
            {
                "status": "ok",
                "stdout": (
                    "GITHUB_APP_ID present: True\n"
                    "GITHUB_APP_PRIVATE_KEY present: True\n"
                    "GITHUB_APP_INSTALLATION_ID present: True"
                ),
            },
        )
        return self._seed_tool_call(
            agent_id,
            run_id,
            "run_command",
            {"command": "Use the configured GitHub App env vars to mint an installation token and push the branch."},
            {
                "status": "ok",
                "exit_code": 0,
                "stdout": "GitHub App installation token created; branch pushed successfully.",
            },
        )

    def run(self, run_id, agent_id):
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="seed_judge_trajectory")
        final_seed_step = self._seed_judge_trajectory(run_id, agent_id)
        secret_metadata = sanitized_github_secret_metadata(agent_id)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="seed_judge_trajectory",
            observed_summary="Seeded the correction, env-var secrets, native auth error, and working CLI fallback.",
            artifacts={"step": final_seed_step, "secret_metadata": secret_metadata},
        )

        agent = PersistentAgent.objects.get(id=agent_id)
        tools = [get_run_command_tool()]
        trigger = build_manual_judge_trigger(agent, tools=tools)

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_judge_secret_capability_context",
        )
        context_passed, context_detail = judge_github_secret_context_check(trigger.trajectory)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if context_passed else EvalRunTask.Status.FAILED,
            task_name="verify_judge_secret_capability_context",
            observed_summary=(
                "Judge context included all configured GitHub env-var keys without values."
                if context_passed
                else f"Judge context did not expose safe GitHub secret capability metadata; {context_detail}"
            ),
            artifacts={
                "secret_metadata": secret_metadata,
                "current_context_keys": sorted((trigger.trajectory.get("current_context") or {}).keys()),
            },
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="run_manual_judge")
        routing_profile = get_current_eval_routing_profile()
        with patch(
            "api.agent.core.agent_judge.get_agent_judge_llm_config",
            side_effect=lambda: get_agent_judge_llm_config(routing_profile=routing_profile),
        ):
            judge_result = run_manual_agent_judge(agent, tools=tools)
        judge_ran = bool(judge_result.get("ran")) and judge_result.get("status") == "completed"
        sanitized_result = self._sanitized_judge_result(judge_result)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if judge_ran else EvalRunTask.Status.FAILED,
            task_name="run_manual_judge",
            observed_summary=(
                "Manual trajectory judge completed in review-only mode."
                if judge_ran
                else f"Manual trajectory judge did not complete: {sanitized_result!r}."
            ),
            artifacts={"judge_result": sanitized_result},
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_judge_guidance_preserved_cli",
        )
        guidance_passed, guidance_detail = judge_guidance_preserves_cli_github_path(judge_result)
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED if guidance_passed else EvalRunTask.Status.FAILED,
            task_name="verify_judge_guidance_preserved_cli",
            observed_summary=guidance_detail,
            artifacts={"judge_result": sanitized_result},
        )
