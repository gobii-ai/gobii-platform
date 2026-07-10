import json
import re
from typing import Any

import sqlparse

from api.models import PersistentAgentHumanInputRequest, PersistentAgentToolCall


SQL_MUTATION_RE = re.compile(r"\b(insert|update|delete|replace|alter|drop|create)\b", re.IGNORECASE)
PLANNING_STATE_TABLE_NAMES = {
    "__agent_config",
}
EVAL_BOOKKEEPING_TABLE_NAMES = {
    "__agent_config",
    "__messages",
    "__tool_results",
}
AGENT_CONFIG_FIELD_PATTERNS = {
    "charter": re.compile(r"\bcharter\b", re.IGNORECASE),
    "schedule": re.compile(r"\bschedule\b", re.IGNORECASE),
}
AGENT_CONFIG_UPDATE_RE = re.compile(
    r"\bupdate\s+__agent_config\b.*?\bset\b(?P<assignments>.*?)(?:\bwhere\b|\breturning\b|$)",
    re.IGNORECASE | re.DOTALL,
)
AGENT_CONFIG_INSERT_RE = re.compile(
    r"\b(?:insert|replace)\s+(?:or\s+\w+\s+)?into\s+__agent_config\s*\((?P<columns>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
FAILED_RESULT_STATUSES = {
    "error",
    "failed",
    "failure",
    "warning",
    "pending",
    "cancelled",
    "canceled",
}
SUCCESS_RESULT_STATUSES = {
    "ok",
    "success",
    "complete",
    "completed",
    "sent",
    "queued",
}


def split_sql_statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sqlparse.split(sql or "") if statement.strip()]


def sql_mentions_planning_state(statement: str) -> bool:
    lowered = statement.lower()
    return any(table in lowered for table in PLANNING_STATE_TABLE_NAMES)


def sql_mentions_eval_bookkeeping(statement: str) -> bool:
    lowered = statement.lower()
    return any(table in lowered for table in EVAL_BOOKKEEPING_TABLE_NAMES)


def sql_mutates(statement: str) -> bool:
    return bool(SQL_MUTATION_RE.search(statement or ""))


def sql_mutates_planning_state(statement: str) -> bool:
    return sql_mentions_planning_state(statement) and sql_mutates(statement)


def sqlite_batch_sql(tool_call) -> str:
    if tool_call.tool_name != "sqlite_batch":
        return ""
    params = tool_call.tool_params or {}
    return str(params.get("sql") or "")


def sqlite_batch_mutates_planning_state(tool_call) -> bool:
    sql = sqlite_batch_sql(tool_call)
    if not sql:
        return False
    return any(sql_mutates_planning_state(statement) for statement in split_sql_statements(sql))


def sqlite_batch_is_only_planning_state_mutation(tool_call) -> bool:
    sql = sqlite_batch_sql(tool_call)
    if not sql:
        return False

    statements = split_sql_statements(sql)
    if not statements:
        return False

    mutating_config_statements = [
        statement for statement in statements if sql_mutates_planning_state(statement)
    ]
    if not mutating_config_statements:
        return False

    # A batch like "UPDATE __agent_config...; CREATE TABLE leads..." must still
    # count as real SQLite work for tool-choice evals. Only pure config mutation
    # batches are ignored as eval bookkeeping noise.
    return all(sql_mentions_planning_state(statement) for statement in statements)


def sqlite_batch_is_only_planning_state_read(tool_call) -> bool:
    sql = sqlite_batch_sql(tool_call)
    if not sql:
        return False

    statements = split_sql_statements(sql)
    if not statements:
        return False

    return all(sql_mentions_planning_state(statement) for statement in statements) and not any(
        sql_mutates(statement) for statement in statements
    )


def sqlite_batch_is_only_eval_bookkeeping_read(tool_call) -> bool:
    sql = sqlite_batch_sql(tool_call)
    if not sql:
        return False

    statements = split_sql_statements(sql)
    if not statements:
        return False

    return all(sql_mentions_eval_bookkeeping(statement) for statement in statements) and not any(
        sql_mutates(statement) for statement in statements
    )


def sqlite_batch_mutates_agent_config_field(tool_call, field_name: str) -> bool:
    if field_name not in AGENT_CONFIG_FIELD_PATTERNS:
        return False
    sql = sqlite_batch_sql(tool_call)
    if not sql:
        return False
    return any(
        sql_mutates_planning_state(statement)
        and _statement_assigns_agent_config_field(statement, field_name)
        for statement in split_sql_statements(sql)
    )


def _normalized_sql_identifier(value: str) -> str:
    return value.strip().strip('"`[]').lower()


def _statement_assigns_agent_config_field(statement: str, field_name: str) -> bool:
    field = field_name.lower()
    update_match = AGENT_CONFIG_UPDATE_RE.search(statement or "")
    if update_match:
        assignments = update_match.group("assignments")
        return bool(
            re.search(
                rf'(?<![\w"`\]])["`\[]?{re.escape(field)}["`\]]?\s*=',
                assignments,
                re.IGNORECASE,
            )
        )

    insert_match = AGENT_CONFIG_INSERT_RE.search(statement or "")
    if insert_match:
        columns = [
            _normalized_sql_identifier(column)
            for column in insert_match.group("columns").split(",")
        ]
        return field in columns

    return False


def _params_match(actual_params: dict[str, Any], expected_params: dict[str, Any]) -> bool:
    return all(actual_params.get(key) == value for key, value in expected_params.items())


def _has_required_param_any(actual_params: dict[str, Any], required_param_names: Any) -> bool:
    if not isinstance(required_param_names, (list, tuple, set)):
        return True
    names = [name for name in required_param_names if isinstance(name, str) and name]
    if not names:
        return True
    return any(name in actual_params for name in names)


def _is_relevant_call(tool_call, policy: dict[str, Any]) -> bool:
    ignored_tool_names = set(policy.get("ignored_tool_names") or ())
    if tool_call.tool_name in ignored_tool_names:
        return False
    if policy.get("ignore_sqlite_eval_bookkeeping_reads", True) and sqlite_batch_is_only_eval_bookkeeping_read(tool_call):
        return False
    if (
        policy.get("ignore_sqlite_agent_config_mutations", True)
        and sqlite_batch_is_only_planning_state_mutation(tool_call)
    ):
        return False
    return True


def _expected_condition_matches_call(
    tool_call,
    condition: dict[str, Any],
    policy: dict[str, Any],
) -> bool:
    tool_name = condition.get("tool_name")
    expected_params = condition.get("params") or {}
    candidate_tool_names = {tool_name, *condition.get("alternatives", [])}

    tool_alternatives = policy.get("accepted_tool_alternatives") or {}
    candidate_tool_names.update(tool_alternatives.get(tool_name) or [])

    if tool_call.tool_name not in candidate_tool_names:
        return False
    actual_params = tool_call.tool_params or {}
    if expected_params and not _params_match(actual_params, expected_params):
        return False
    if not _has_required_param_any(actual_params, condition.get("required_params_any")):
        return False
    if condition.get("after_execution") and (
        _tool_call_was_skipped(tool_call) or not tool_call_has_succeeded(tool_call)
    ):
        return False
    if condition.get("after_finish") and not _tool_call_has_finished(tool_call):
        return False

    config_field = condition.get("agent_config_field")
    if config_field and tool_call.tool_name == "sqlite_batch":
        return sqlite_batch_mutates_agent_config_field(tool_call, config_field)

    return True


def _get_eval_tool_calls(eval_run_id: str, policy: dict[str, Any]):
    calls = (
        PersistentAgentToolCall.objects
        .filter(step__eval_run_id=eval_run_id)
        .select_related("step")
        .order_by("step__created_at", "step__id")
    )
    return [call for call in calls if _is_relevant_call(call, policy)]


def _tool_call_has_finished(tool_call) -> bool:
    return str(getattr(tool_call, "status", "") or "").lower() in {"complete", "error"}


def tool_call_has_succeeded(tool_call) -> bool:
    if str(getattr(tool_call, "status", "") or "").lower() != "complete":
        return False
    try:
        result = json.loads(getattr(tool_call, "result", "") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(result, dict):
        return False

    nested_result = result.get("result")
    result_payloads = [result]
    if isinstance(nested_result, dict):
        result_payloads.append(nested_result)

    return (
        str(result.get("status") or "ok").casefold() in SUCCESS_RESULT_STATUSES
        and all(
            str(payload.get("status") or "").casefold() not in FAILED_RESULT_STATUSES
            and not payload.get("error")
            for payload in result_payloads
        )
    )


def _tool_call_was_skipped(tool_call) -> bool:
    try:
        parsed = json.loads(getattr(tool_call, "result", "") or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and parsed.get("skipped") is True


def should_stop_for_eval_policy(eval_run_id: str | None, policy: dict[str, Any] | None) -> tuple[bool, str]:
    if not eval_run_id or not policy:
        return False, ""

    calls = _get_eval_tool_calls(eval_run_id, policy)
    first_relevant = policy.get("stop_on_first_relevant_tool")
    if first_relevant and calls:
        return True, f"first relevant tool call observed: {calls[0].tool_name}"

    max_relevant_tool_calls = int(policy.get("max_relevant_tool_calls") or 0)
    finished_calls = [call for call in calls if _tool_call_has_finished(call)]
    if max_relevant_tool_calls > 0 and len(finished_calls) >= max_relevant_tool_calls:
        return True, f"relevant tool call budget reached: {len(finished_calls)}/{max_relevant_tool_calls}"

    stop_on_tool_names = set(policy.get("stop_on_tool_names") or ())
    if stop_on_tool_names:
        for call in calls:
            if call.tool_name in stop_on_tool_names:
                return True, f"terminal tool call observed: {call.tool_name}"

    stop_after_execution_tool_names = set(policy.get("stop_on_tool_names_after_execution") or ())
    if stop_after_execution_tool_names:
        for call in calls:
            if (
                call.tool_name in stop_after_execution_tool_names
                and not _tool_call_was_skipped(call)
                and tool_call_has_succeeded(call)
            ):
                return True, f"terminal tool call completed: {call.tool_name}"

    stop_after_finish_tool_names = set(policy.get("stop_on_tool_names_after_finish") or ())
    if stop_after_finish_tool_names:
        for call in calls:
            if (
                call.tool_name in stop_after_finish_tool_names
                and not _tool_call_was_skipped(call)
                and _tool_call_has_finished(call)
            ):
                return True, f"terminal tool call finished: {call.tool_name}"

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
            if not tool_name:
                return False, ""
            if not any(
                _expected_condition_matches_call(call, expected, policy)
                for call in calls
            ):
                return False, ""
        return True, "all terminal expected tool calls observed"

    return False, ""
