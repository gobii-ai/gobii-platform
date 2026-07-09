import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

from django.utils import timezone

from api.agent.system_skills.service import enable_system_skills
from api.evals.base import EvalScenario
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry
from api.models import EvalRunTask, PersistentAgent, PersistentAgentMessage, PersistentAgentSystemStep, PersistentAgentStep, PersistentAgentToolCall
from api.services.native_integrations import get_native_integration_provider, save_native_integration_credentials
from api.services.persistent_agent_secrets import resolve_global_secret_owner_for_agent


@dataclass(frozen=True)
class HttpRequestExpectation:
    name: str
    url_terms: tuple[str, ...]
    method: str = "GET"
    body_terms: tuple[str, ...] = ()
    body_term_groups: tuple[tuple[str, ...], ...] = ()
    allowed_statuses: tuple[str, ...] = ("complete",)


@dataclass(frozen=True)
class NativeHttpCase:
    slug: str
    prompt: str
    description: str
    http_rules: tuple[dict[str, Any], ...]
    expected_http_requests: tuple[HttpRequestExpectation, ...]
    forbidden_url_terms: tuple[tuple[str, ...], ...] = ()
    response_term_groups: tuple[tuple[str, ...], ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)

    def mock_config(self) -> dict[str, dict[str, Any]]:
        return {
            "http_request": {
                "rules": list(self.http_rules),
                "default": {
                    "status": "error",
                    "status_code": 404,
                    "message": "Unexpected native HTTP eval URL.",
                    "content": {"ok": False},
                },
            }
        }


def json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def tool_calls_for_run(run_id: str, *, after=None, tool_names=None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def decoded_url(call: PersistentAgentToolCall) -> str:
    params = call.tool_params or {}
    return unquote_plus(str(params.get("url") or "")).lower()


def query_value(call: PersistentAgentToolCall, key: str) -> str:
    raw_url = str((call.tool_params or {}).get("url") or "")
    values = parse_qs(urlparse(raw_url).query).get(key) or []
    if not values:
        return ""
    return unquote_plus(str(values[0] or "")).strip().lower()


def request_body(call: PersistentAgentToolCall) -> str:
    body = (call.tool_params or {}).get("body")
    if isinstance(body, str):
        return body.lower()
    if body is None:
        return ""
    return json_body(body).lower()


def request_method(call: PersistentAgentToolCall) -> str:
    return str((call.tool_params or {}).get("method") or "GET").strip().upper()


def call_matches_expectation(call: PersistentAgentToolCall, expectation: HttpRequestExpectation) -> bool:
    allowed_statuses = {status.lower() for status in expectation.allowed_statuses}
    body = request_body(call)
    if str(getattr(call, "status", "") or "").lower() not in allowed_statuses:
        return False
    if request_method(call) != expectation.method.upper():
        return False
    if not all(term.lower() in decoded_url(call) for term in expectation.url_terms):
        return False
    if not all(term.lower() in body for term in expectation.body_terms):
        return False
    if not all(any(term.lower() in body for term in group) for group in expectation.body_term_groups):
        return False
    return True


def call_matches_url_terms(call: PersistentAgentToolCall, url_terms: tuple[str, ...]) -> bool:
    return all(term.lower() in decoded_url(call) for term in url_terms)


def response_contains_term(body: str, term: str) -> bool:
    body_lower = body.lower()
    term_lower = term.lower()
    if term_lower in body_lower:
        return True

    compact_term = re.sub(r"[$,\s]", "", term_lower)
    if compact_term.isdigit():
        compact_body = re.sub(r"[$,\s]", "", body_lower)
        return compact_term in compact_body

    return False


class NativeHttpScenarioBase(EvalScenario, ScenarioExecutionTools):
    system_skill_key = ""
    system_skill_name = "native HTTP"
    native_provider_key = ""
    forbidden_tool_names: tuple[str, ...] = ()
    forbidden_tool_prefixes: tuple[str, ...] = ()
    allowed_tool_names: tuple[str, ...] = ("http_request", "send_chat_message")
    max_relevant_tool_calls = 12
    expected_requests_summary = "Agent completed the expected native REST request(s)."
    forbidden_pass_summary = "Agent avoided forbidden tools and URLs."
    response_pass_summary = "Final response included the expected mocked result or setup guidance."
    no_response_terms_summary = "No response content terms configured for this case."
    case: NativeHttpCase | None = None

    def _case(self) -> NativeHttpCase:
        if self.case is None:
            raise ValueError(f"{type(self).__name__}.case must be set.")
        return self.case

    def _seed_prior_processing_run(self, agent_id: str) -> None:
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return

        prior_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Process events",
        )
        PersistentAgentSystemStep.objects.create(
            step=prior_step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    def _should_seed_native_connection(self) -> bool:
        case = self._case()
        return bool(self.native_provider_key) and "missing_connection" not in case.tags

    def _seed_native_connection(self, agent: PersistentAgent) -> None:
        if not self._should_seed_native_connection():
            return

        provider = get_native_integration_provider(self.native_provider_key)
        owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
        save_native_integration_credentials(
            provider,
            owner_user,
            owner_org,
            {
                "provider_key": provider.key,
                "auth_type": "oauth2",
                "access_token": f"eval-{provider.key}-access-token",
                "refresh_token": f"eval-{provider.key}-refresh-token",
                "token_type": "Bearer",
                "scope": provider.scope_string,
                "expires_at": (timezone.now() + timedelta(hours=1)).isoformat(),
            },
        )

    def _prepare_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        agent = PersistentAgent.objects.select_related("user", "organization").get(id=agent_id)
        self._seed_native_connection(agent)
        result = enable_system_skills(agent, [self.system_skill_key])
        if result.get("invalid"):
            raise ValueError(f"Could not enable {self.system_skill_name} native system skill: {result}")

    def _eval_stop_policy(self) -> dict[str, Any]:
        return {
            "allowed_tool_names": list(self.allowed_tool_names),
            "ignored_tool_names": ["sleep_until_next_trigger", "update_plan"],
            "stop_on_unexpected_relevant_tool": True,
            "stop_on_tool_names": list(self.forbidden_tool_names),
            "stop_on_tool_names_after_finish": ["send_chat_message"],
            "max_relevant_tool_calls": self.max_relevant_tool_calls,
        }

    def _record_expected_http_requests(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_expected_http_requests",
        )
        http_calls = tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=["http_request"])
        missing = [
            expectation.name
            for expectation in case.expected_http_requests
            if not any(call_matches_expectation(call, expectation) for call in http_calls)
        ]
        if not missing:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_expected_http_requests",
                observed_summary=self.expected_requests_summary,
                artifacts={"step": http_calls[0].step} if http_calls else {},
            )
            return

        seen = [
            {
                "method": request_method(call),
                "url": (call.tool_params or {}).get("url"),
                "body": request_body(call)[:500],
            }
            for call in http_calls
        ]
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_expected_http_requests",
            observed_summary=f"Missing expected HTTP request(s): {missing}; saw {seen}.",
            artifacts={"step": http_calls[0].step} if http_calls else {},
        )

    def _extra_checks(self, run_id: str, inbound) -> None:
        return None

    def _record_forbidden_absence(self, run_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_forbidden_tools",
        )
        calls = tool_calls_for_run(run_id, after=inbound.timestamp)
        bad_calls = [
            call
            for call in calls
            if any(call.tool_name.startswith(prefix) for prefix in self.forbidden_tool_prefixes)
            or call.tool_name in self.forbidden_tool_names
            or (
                call.tool_name == "http_request"
                and any(call_matches_url_terms(call, terms) for terms in case.forbidden_url_terms)
            )
        ]
        if bad_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_forbidden_tools",
                observed_summary=(
                    "Agent used forbidden tool or URL: "
                    f"{[(call.tool_name, (call.tool_params or {}).get('url')) for call in bad_calls]}."
                ),
                artifacts={"step": bad_calls[0].step},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_no_forbidden_tools",
            observed_summary=self.forbidden_pass_summary,
        )

    def _record_response(self, run_id: str, agent_id: str, inbound) -> None:
        case = self._case()
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_response")
        if not case.response_term_groups:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_response",
                observed_summary=self.no_response_terms_summary,
            )
            return

        final_response = (
            PersistentAgentMessage.objects
            .filter(owner_agent_id=agent_id, is_outbound=True, timestamp__gt=inbound.timestamp)
            .order_by("-timestamp")
            .first()
        )
        body = final_response.body if final_response else ""
        missing_groups = [
            terms
            for terms in case.response_term_groups
            if not any(response_contains_term(body, term) for term in terms)
        ]
        if not missing_groups:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_response",
                observed_summary=self.response_pass_summary,
                artifacts={"message": final_response} if final_response else {},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_response",
            observed_summary=f"Final response missing expected term group(s) {missing_groups}; body={body[:800]!r}.",
            artifacts={"message": final_response} if final_response else {},
        )

    def run(self, run_id: str, agent_id: str) -> None:
        case = self._case()
        self._prepare_agent(agent_id)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=case.mock_config(),
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

        self._record_expected_http_requests(run_id, inbound)
        self._extra_checks(run_id, inbound)
        self._record_forbidden_absence(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


def register_native_http_scenarios(cases, scenario_class):
    for case in cases:
        scenario_type = type(
            "".join(part.title() for part in case.slug.split("_")) + "Scenario",
            (scenario_class,),
            {
                "slug": case.slug,
                "description": case.description,
                "tags": scenario_class.tags + case.tags,
                "case": case,
            },
        )
        ScenarioRegistry.register(scenario_type())
