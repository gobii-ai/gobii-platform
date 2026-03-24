import json
import logging

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from api.agent.tools.custom_tools import load_custom_tool_bridge_payload
from api.agent.tools.tracked_runtime import execute_tracked_runtime_tool_call
from api.models import PersistentAgent, PersistentAgentCustomTool, PersistentAgentStep

logger = logging.getLogger(__name__)


def _json_safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, default=str))


def _extract_bearer_token(request) -> str:
    header = request.headers.get("Authorization", "")
    if not isinstance(header, str):
        return ""
    if not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()


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
        return JsonResponse(
            {
                "status": "error",
                "message": "Custom tools cannot call themselves recursively.",
            }
        )

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
    return JsonResponse(_json_safe(result), safe=isinstance(result, dict))
