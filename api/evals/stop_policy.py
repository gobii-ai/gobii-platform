import re
from typing import Any

from api.models import PersistentAgentHumanInputRequest, PersistentAgentToolCall


SQL_MUTATION_RE = re.compile(r"\b(insert|update|delete|replace|alter|drop|create)\b", re.IGNORECASE)
PLANNING_STATE_TABLE_NAMES = {
    "__agent_config",
}


def sqlite_batch_mutates_planning_state(tool_call) -> bool:
    if tool_call.tool_name != "sqlite_batch":
        return False
    params = tool_call.tool_params or {}
    sql = str(params.get("sql") or "")
    if not sql:
        return False
    lowered = sql.lower()
    if not any(table in lowered for table in PLANNING_STATE_TABLE_NAMES):
        return False
    return bool(SQL_MUTATION_RE.search(sql))


def _params_match(actual_params: dict[str, Any], expected_params: dict[str, Any]) -> bool:
    return all(actual_params.get(key) == value for key, value in expected_params.items())


def _is_relevant_call(tool_call, policy: dict[str, Any]) -> bool:
    ignored_tool_names = set(policy.get("ignored_tool_names") or ())
    if tool_call.tool_name in ignored_tool_names:
        return False
    if policy.get("ignore_sqlite_agent_config_mutations", True) and sqlite_batch_mutates_planning_state(tool_call):
        return False
    return True


def _get_eval_tool_calls(eval_run_id: str, policy: dict[str, Any]):
    calls = (
        PersistentAgentToolCall.objects
        .filter(step__eval_run_id=eval_run_id)
        .select_related("step")
        .order_by("step__created_at", "step__id")
    )
    return [call for call in calls if _is_relevant_call(call, policy)]


def should_stop_for_eval_policy(eval_run_id: str | None, policy: dict[str, Any] | None) -> tuple[bool, str]:
    if not eval_run_id or not policy:
        return False, ""

    calls = _get_eval_tool_calls(eval_run_id, policy)
    first_relevant = policy.get("stop_on_first_relevant_tool")
    if first_relevant and calls:
        return True, f"first relevant tool call observed: {calls[0].tool_name}"

    stop_on_tool_names = set(policy.get("stop_on_tool_names") or ())
    if stop_on_tool_names:
        for call in calls:
            if call.tool_name in stop_on_tool_names:
                return True, f"terminal tool call observed: {call.tool_name}"

    if policy.get("stop_on_unexpected_relevant_tool"):
        allowed_tool_names = set(policy.get("allowed_tool_names") or ())
        for call in calls:
            if call.tool_name not in allowed_tool_names:
                return True, f"unexpected relevant tool call observed: {call.tool_name}"

    if policy.get("stop_on_sqlite_agent_config_mutation"):
        for call in (
            PersistentAgentToolCall.objects
            .filter(step__eval_run_id=eval_run_id, tool_name="sqlite_batch")
            .select_related("step")
            .order_by("step__created_at", "step__id")
        ):
            if sqlite_batch_mutates_planning_state(call):
                return True, "SQLite agent config mutation observed"

    if policy.get("stop_on_human_input_request"):
        if PersistentAgentHumanInputRequest.objects.filter(originating_step__eval_run_id=eval_run_id).exists():
            return True, "tracked human input request observed"

    expected_calls = list(policy.get("stop_when_all_seen") or ())
    if expected_calls:
        for expected in expected_calls:
            tool_name = expected.get("tool_name")
            expected_params = expected.get("params") or {}
            if not tool_name:
                return False, ""
            if not any(
                call.tool_name == tool_name
                and _params_match(call.tool_params or {}, expected_params)
                for call in calls
            ):
                return False, ""
        return True, "all terminal expected tool calls observed"

    return False, ""
