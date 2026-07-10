import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

from django.utils import timezone

from api.agent.system_skills.service import enable_system_skills
from api.evals.base import EvalScenario, ScenarioTask
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
    allowed_methods: tuple[str, ...] = ()
    body_terms: tuple[str, ...] = ()
    body_term_groups: tuple[tuple[str, ...], ...] = ()
    forbidden_body_terms: tuple[str, ...] = ()
    body_object_subsets: tuple[dict[str, Any], ...] = ()
    body_array_fields: tuple[str, ...] = ()
    body_string_fields: tuple[str, ...] = ()
    forbidden_body_fields: tuple[str, ...] = ()
    exact_path: str = ""
    excluded_url_terms: tuple[str, ...] = ()
    allowed_statuses: tuple[str, ...] = ("complete",)
    min_calls: int = 1
    max_calls: int | None = 1


@dataclass(frozen=True)
class NativeHttpCase:
    slug: str
    prompt: str
    description: str
    http_rules: tuple[dict[str, Any], ...]
    expected_http_requests: tuple[HttpRequestExpectation, ...]
    allowed_http_requests: tuple[HttpRequestExpectation, ...] = ()
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


def tool_calls_for_run(run_id: str, *, after=None, tool_names=None, include_skipped: bool = False):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if not include_skipped:
        queryset = queryset.exclude(status="skipped")
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


def request_json_body(call: PersistentAgentToolCall) -> Any:
    body = (call.tool_params or {}).get("body")
    if isinstance(body, (dict, list)):
        return body
    if not isinstance(body, str):
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _json_scalar_matches(actual: Any, expected: Any, *, allow_qualified: bool = False) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (str, int, float)) and isinstance(expected, (str, int, float)):
        actual_text = str(actual).strip().casefold()
        expected_text = str(expected).strip().casefold()
        return actual_text == expected_text or (
            allow_qualified
            and isinstance(actual, str)
            and isinstance(expected, str)
            and expected_text in {part.strip() for part in actual_text.split(",")}
        )
    return actual == expected


def _object_contains_subset(value: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(value, dict):
        return False
    for key, expected_value in expected.items():
        if key not in value:
            return False
        actual_value = value[key]
        allow_qualified = "location" in str(key).casefold()
        if isinstance(expected_value, dict):
            if not _object_contains_subset(actual_value, expected_value):
                return False
        elif isinstance(expected_value, (list, tuple)):
            if not isinstance(actual_value, list) or not all(
                any(
                    _json_scalar_matches(actual_item, expected_item, allow_qualified=allow_qualified)
                    for actual_item in actual_value
                )
                for expected_item in expected_value
            ):
                return False
        elif not _json_scalar_matches(actual_value, expected_value, allow_qualified=allow_qualified):
            return False
    return True


def json_tree_contains_object_subset(value: Any, expected: dict[str, Any]) -> bool:
    if _object_contains_subset(value, expected):
        return True
    if isinstance(value, dict):
        return any(json_tree_contains_object_subset(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(json_tree_contains_object_subset(item, expected) for item in value)
    return False


def call_matches_expectation(call: PersistentAgentToolCall, expectation: HttpRequestExpectation) -> bool:
    allowed_statuses = {status.lower() for status in expectation.allowed_statuses}
    body = request_body(call)
    if str(getattr(call, "status", "") or "").lower() not in allowed_statuses:
        return False
    allowed_methods = expectation.allowed_methods or (expectation.method,)
    if request_method(call) not in {method.upper() for method in allowed_methods}:
        return False
    url = decoded_url(call)
    if not all(term.lower() in url for term in expectation.url_terms):
        return False
    if any(term.lower() in url for term in expectation.excluded_url_terms):
        return False
    if expectation.exact_path:
        raw_url = str((call.tool_params or {}).get("url") or "")
        path = unquote_plus(urlparse(raw_url).path).lower().rstrip("/") or "/"
        expected_path = expectation.exact_path.lower().rstrip("/") or "/"
        if path != expected_path:
            return False
    if not all(term.lower() in body for term in expectation.body_terms):
        return False
    if not all(any(term.lower() in body for term in group) for group in expectation.body_term_groups):
        return False
    if any(term.lower() in body for term in expectation.forbidden_body_terms):
        return False
    if expectation.body_object_subsets:
        json_body_value = request_json_body(call)
        if not all(
            json_tree_contains_object_subset(json_body_value, expected)
            for expected in expectation.body_object_subsets
        ):
            return False
    if expectation.body_array_fields or expectation.body_string_fields or expectation.forbidden_body_fields:
        json_body_value = request_json_body(call)
        if not isinstance(json_body_value, dict):
            return False
        if not all(isinstance(json_body_value.get(field), list) for field in expectation.body_array_fields):
            return False
        if not all(isinstance(json_body_value.get(field), str) for field in expectation.body_string_fields):
            return False
        if any(field in json_body_value for field in expectation.forbidden_body_fields):
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
        return bool(re.search(rf"(?<!\d){re.escape(compact_term)}(?!\d)", compact_body))

    return False


def message_claims_provider_ready(body: Any, provider_name: str) -> bool:
    text = " ".join(str(body or "").lower().split())
    text = re.sub(r"[*_`]", "", text)
    provider = re.escape(provider_name.lower())
    patterns = (
        re.compile(
            rf"\b{provider}\b(?:['’]s)?(?:\s+(?:native\s+)?(?:integration|connection|oauth))?\s+"
            r"(?:is|looks|appears|seems)\s+(?:fully\s+)?(?:connected|ready|active|available|working)\b"
        ),
        re.compile(rf"\b{provider}\b.{{0,36}}\bconnected\s+and\s+ready\b"),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 24):match.start()]
            if re.search(r"\b(?:if|whether)\s*$", prefix):
                continue
            sentence_tail = text[match.end():match.end() + 8]
            if "?" in sentence_tail:
                continue
            contrast = text[match.end():match.end() + 120]
            if re.search(
                r"\b(?:but|however)\b.{0,96}\b(?:disagrees?|contradicts?|401|not connected|expired|misconfigured)\b",
                contrast,
            ):
                continue
            return True
    return False


def false_readiness_claims_before_first_http(calls, provider_name: str):
    first_http_index = next(
        (index for index, call in enumerate(calls) if call.tool_name == "http_request"),
        len(calls),
    )
    return [
        call
        for call in calls[:first_http_index]
        if call.tool_name == "send_chat_message"
        and message_claims_provider_ready((call.tool_params or {}).get("body"), provider_name)
    ]


def false_readiness_claims(calls, provider_name: str):
    return [
        call
        for call in calls
        if call.tool_name == "send_chat_message"
        and message_claims_provider_ready((call.tool_params or {}).get("body"), provider_name)
    ]


def validate_http_call_set(
    calls: list[PersistentAgentToolCall],
    expectations: tuple[HttpRequestExpectation, ...],
) -> tuple[list[str], list[dict[str, Any]]]:
    violations = []
    unmatched_calls = []
    match_counts = [0 for _expectation in expectations]
    for call in calls:
        matching_indexes = [
            index
            for index, expectation in enumerate(expectations)
            if call_matches_expectation(call, expectation)
        ]
        if matching_indexes:
            # A narrow required request should win over an explicitly allowed
            # broad preflight when both happen to match the same call.
            best_index = max(
                matching_indexes,
                key=lambda index: (
                    len(expectations[index].url_terms)
                    + len(expectations[index].body_terms)
                    + len(expectations[index].body_term_groups)
                    + len(expectations[index].forbidden_body_terms)
                    + bool(expectations[index].exact_path),
                    expectations[index].min_calls,
                ),
            )
            match_counts[best_index] += 1
            continue
        unmatched_calls.append(
            {
                "method": request_method(call),
                "url": (call.tool_params or {}).get("url"),
                "body": request_body(call)[:500],
            }
        )

    if unmatched_calls:
        violations.append(f"Undeclared HTTP request(s): {unmatched_calls}")

    for expectation, match_count in zip(expectations, match_counts, strict=True):
        if match_count < expectation.min_calls:
            violations.append(
                f"{expectation.name} expected at least {expectation.min_calls} call(s), saw {match_count}"
            )
        if expectation.max_calls is not None and match_count > expectation.max_calls:
            violations.append(
                f"{expectation.name} allowed at most {expectation.max_calls} call(s), saw {match_count}"
            )

    return violations, unmatched_calls


def validate_http_attempt_efficiency(
    calls: list[PersistentAgentToolCall],
) -> list[str]:
    skipped_calls = [
        call
        for call in calls
        if str(getattr(call, "status", "") or "").casefold() == "skipped"
    ]
    if not skipped_calls:
        return []
    attempts = [
        {
            "method": request_method(call),
            "url": (call.tool_params or {}).get("url"),
        }
        for call in skipped_calls
    ]
    return [
        "Redundant HTTP attempt(s) were skipped by runtime deduplication: "
        f"{attempts}"
    ]


class NativeHttpScenarioBase(EvalScenario, ScenarioExecutionTools):
    fingerprint_dependencies = (
        json_body,
        tool_calls_for_run,
        decoded_url,
        query_value,
        request_body,
        request_method,
        request_json_body,
        _json_scalar_matches,
        _object_contains_subset,
        json_tree_contains_object_subset,
        call_matches_expectation,
        call_matches_url_terms,
        response_contains_term,
        message_claims_provider_ready,
        false_readiness_claims_before_first_http,
        false_readiness_claims,
        validate_http_call_set,
        validate_http_attempt_efficiency,
    )
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
        return bool(self.native_provider_key)

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
        all_http_calls = tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            tool_names=["http_request"],
            include_skipped=True,
        )
        http_calls = [
            call
            for call in all_http_calls
            if str(call.status or "").casefold() != "skipped"
        ]
        declared_requests = (*case.expected_http_requests, *case.allowed_http_requests)
        violations, unmatched_calls = validate_http_call_set(http_calls, declared_requests)
        violations.extend(validate_http_attempt_efficiency(all_http_calls))
        if not violations:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_expected_http_requests",
                observed_summary=self.expected_requests_summary,
                artifacts={"step": http_calls[0].step} if http_calls else {},
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_expected_http_requests",
            observed_summary="; ".join(violations),
            artifacts={
                **({"step": http_calls[0].step} if http_calls else {}),
                "unmatched_http_calls": unmatched_calls,
            },
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

    def _record_no_false_connection_claim(self, run_id: str, inbound) -> None:
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_false_connection_claim",
        )
        calls = tool_calls_for_run(run_id, after=inbound.timestamp)
        bad_calls = false_readiness_claims(calls, self.system_skill_name)
        bad_messages = [
            message
            for message in PersistentAgentMessage.objects.filter(
                owner_agent_id=inbound.owner_agent_id,
                is_outbound=True,
                timestamp__gt=inbound.timestamp,
            )
            if message_claims_provider_ready(message.body, self.system_skill_name)
        ]
        if bad_calls or bad_messages:
            claims = list(dict.fromkeys(
                [str((call.tool_params or {}).get("body") or "")[:500] for call in bad_calls]
                + [str(message.body or "")[:500] for message in bad_messages]
            ))
            artifact = bad_calls[0].step if bad_calls else bad_messages[0]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_false_connection_claim",
                observed_summary=f"Agent falsely claimed {self.system_skill_name} was connected or ready despite the failed API call: {claims}.",
                artifacts={
                    "step" if bad_calls else "message": artifact,
                    "false_connection_claims": claims,
                },
            )
            return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_no_false_connection_claim",
            observed_summary=(
                f"Agent made no false claim that {self.system_skill_name} was connected or ready."
            ),
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
        if "missing_connection" in case.tags:
            self._record_no_false_connection_claim(run_id, inbound)
        self._extra_checks(run_id, inbound)
        self._record_forbidden_absence(run_id, inbound)
        self._record_response(run_id, agent_id, inbound)


def register_native_http_scenarios(cases, scenario_class):
    for case in cases:
        tasks = list(scenario_class.tasks)
        if "missing_connection" in case.tags:
            expected_request_index = next(
                index
                for index, task in enumerate(tasks)
                if task.name == "verify_expected_http_requests"
            )
            tasks.insert(
                expected_request_index + 1,
                ScenarioTask(
                    name="verify_no_false_connection_claim",
                    assertion_type="tool_call",
                    description="Do not assert provider readiness without successful API evidence.",
                ),
            )
        scenario_type = type(
            "".join(part.title() for part in case.slug.split("_")) + "Scenario",
            (scenario_class,),
            {
                "slug": case.slug,
                "description": case.description,
                "tags": scenario_class.tags + case.tags,
                "case": case,
                "tasks": tasks,
            },
        )
        ScenarioRegistry.register(scenario_type())
