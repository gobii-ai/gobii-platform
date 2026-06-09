import hashlib
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from api.agent.core.agent_judge import maybe_run_agent_judge
from api.agent.tools.custom_tools import (
    CUSTOM_TOOL_BRIDGE_TTL_SECONDS,
    load_custom_tool_bridge_payload,
    read_custom_tool_source_text,
)
from api.agent.tools.tracked_runtime import execute_tracked_runtime_tool_call
from api.models import (
    PersistentAgent,
    PersistentAgentCustomTool,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)

logger = logging.getLogger(__name__)

CUSTOM_TOOL_CHILD_FAILURE_TRIGGER_REASON = "custom_tool_child_failure_budget_exceeded"
CUSTOM_TOOL_ABORT_MESSAGE_TEMPLATE = "Custom tool stopped after {threshold} failed child tool calls."


def _json_safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, default=str))


def _extract_bearer_token(request) -> str:
    header = request.headers.get("Authorization", "")
    if not isinstance(header, str):
        return ""
    if not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()


def _child_failure_limit() -> int:
    return max(1, int(settings.CUSTOM_TOOL_CHILD_FAILURE_LIMIT))


def _execution_cache_key(token: str, payload: dict) -> str:
    parent_step_id = payload.get("parent_step_id")
    if isinstance(parent_step_id, str) and parent_step_id.strip():
        execution_id = f"parent:{parent_step_id.strip()}"
    else:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
        execution_id = f"token:{token_hash}"
    return (
        "custom-tool-bridge:child-failure-budget:"
        f"{payload.get('agent_id')}:{payload.get('tool_id')}:{execution_id}"
    )


def _abort_payload(state: dict | None = None) -> dict:
    threshold = _child_failure_limit()
    failure_count = threshold
    if isinstance(state, dict):
        threshold = int(state.get("threshold") or threshold)
        failure_count = int(state.get("failure_count") or threshold)
    return {
        "status": "error",
        "message": CUSTOM_TOOL_ABORT_MESSAGE_TEMPLATE.format(threshold=threshold),
        "custom_tool_abort": True,
        "failure_count": failure_count,
        "threshold": threshold,
    }


def _is_error_result(result) -> bool:
    return isinstance(result, dict) and str(result.get("status") or "").lower() == "error"


def _get_budget_state(cache_key: str) -> dict:
    state = cache.get(cache_key)
    if isinstance(state, dict):
        return state
    return {
        "failure_count": 0,
        "aborted": False,
        "threshold": _child_failure_limit(),
        "first_failed_tool": "",
        "last_failed_tool": "",
    }


def _record_budget_abort(agent: PersistentAgent, custom_tool: PersistentAgentCustomTool, state: dict) -> None:
    description = (
        f"Custom tool `{custom_tool.tool_name}` stopped after "
        f"{state.get('failure_count')} failed child tool calls."
    )
    step = PersistentAgentStep.objects.create(agent=agent, description=description)
    PersistentAgentSystemStep.objects.create(
        step=step,
        code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        notes=CUSTOM_TOOL_CHILD_FAILURE_TRIGGER_REASON,
    )
    maybe_run_agent_judge(
        agent,
        extra_trigger_reasons=[CUSTOM_TOOL_CHILD_FAILURE_TRIGGER_REASON],
        trigger_context=_custom_tool_judge_trigger_context(agent, custom_tool),
    )


def _custom_tool_judge_trigger_context(agent: PersistentAgent, custom_tool: PersistentAgentCustomTool) -> dict:
    source_text, source_error = read_custom_tool_source_text(agent, custom_tool.source_path)
    source_context = {
        "source_type": "custom_tool_source",
        "tool_name": custom_tool.tool_name,
        "name": custom_tool.name or "",
        "source_path": custom_tool.source_path,
    }
    if source_error:
        source_context["source_error"] = source_error
    else:
        source_context["source_code"] = source_text or ""
    return {
        "custom_tool_sources": [source_context],
    }


def _record_child_tool_failure(
    *,
    cache_key: str,
    agent: PersistentAgent,
    custom_tool: PersistentAgentCustomTool,
    failed_tool_name: str,
) -> dict | None:
    state = _get_budget_state(cache_key)
    if state.get("aborted"):
        return _abort_payload(state)

    failure_count = int(state.get("failure_count") or 0) + 1
    state["failure_count"] = failure_count
    state["threshold"] = _child_failure_limit()
    if not state.get("first_failed_tool"):
        state["first_failed_tool"] = failed_tool_name
    state["last_failed_tool"] = failed_tool_name

    if failure_count >= int(state["threshold"]):
        state["aborted"] = True
        cache.set(cache_key, state, timeout=CUSTOM_TOOL_BRIDGE_TTL_SECONDS)
        _record_budget_abort(agent, custom_tool, state)
        return _abort_payload(state)

    cache.set(cache_key, state, timeout=CUSTOM_TOOL_BRIDGE_TTL_SECONDS)
    return None


@csrf_exempt
@require_POST
def custom_tool_bridge_execute(request):
    token = _extract_bearer_token(request)
    payload = load_custom_tool_bridge_payload(token)
    if payload is None:
        return JsonResponse({"status": "error", "message": "Invalid or expired custom tool token."}, status=403)

    agent = PersistentAgent.objects.filter(id=payload.get("agent_id")).first()
    if agent is None:
        return JsonResponse({"status": "error", "message": "Agent not found."}, status=403)

    custom_tool = PersistentAgentCustomTool.objects.filter(
        id=payload.get("tool_id"),
        agent=agent,
        tool_name=payload.get("tool_name"),
    ).first()
    if custom_tool is None:
        return JsonResponse({"status": "error", "message": "Custom tool not found."}, status=403)

    budget_cache_key = _execution_cache_key(token, payload)
    budget_state = _get_budget_state(budget_cache_key)
    if budget_state.get("aborted"):
        return JsonResponse(_abort_payload(budget_state))

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"status": "error", "message": "Request body must be valid JSON."}, status=400)

    tool_name = body.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return JsonResponse({"status": "error", "message": "tool_name is required."}, status=400)
    tool_name = tool_name.strip()

    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return JsonResponse({"status": "error", "message": "params must be a JSON object."}, status=400)

    if tool_name == custom_tool.tool_name:
        result = {
            "status": "error",
            "message": "Custom tools cannot call themselves recursively.",
        }
        abort = _record_child_tool_failure(
            cache_key=budget_cache_key,
            agent=agent,
            custom_tool=custom_tool,
            failed_tool_name=tool_name,
        )
        return JsonResponse(abort or result)

    parent_step = None
    parent_step_id = payload.get("parent_step_id")
    if isinstance(parent_step_id, str) and parent_step_id.strip():
        parent_step = PersistentAgentStep.objects.filter(
            id=parent_step_id.strip(),
            agent=agent,
        ).select_related("completion", "eval_run").first()

    result, _updated_tools = execute_tracked_runtime_tool_call(
        agent,
        tool_name=tool_name,
        exec_params=params,
        parent_step=parent_step,
    )
    if _is_error_result(result):
        abort = _record_child_tool_failure(
            cache_key=budget_cache_key,
            agent=agent,
            custom_tool=custom_tool,
            failed_tool_name=tool_name,
        )
        if abort is not None:
            return JsonResponse(abort)
    return JsonResponse(_json_safe(result), safe=isinstance(result, dict))
