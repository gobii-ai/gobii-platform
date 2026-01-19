import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from celery import shared_task
from django.db import close_old_connections
from django.db.utils import OperationalError
from django.utils import timezone

from config.redis_client import get_redis_client
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from api.agent.core.budget import AgentBudgetManager
from api.agent.core.llm_config import get_work_task_llm_config
from api.agent.core.llm_utils import run_completion
from api.agent.core.token_usage import compute_cost_breakdown, extract_token_usage
from api.agent.tools.mcp_manager import execute_platform_mcp_tool, get_mcp_manager
from api.agent.work_task_shared import (
    WORK_TASK_ALLOWED_MCP_TOOLS,
    build_work_task_tool_result,
    coerce_summary_payload,
    extract_mcp_server_name,
    serialize_tool_result,
)
from api.models import WorkTask, WorkTaskStep
from api.services.work_task_settings import get_work_task_settings

logger = logging.getLogger(__name__)

_COST_PRECISION = Decimal("0.000001")
_WORK_TASK_REDIS_TTL_SECONDS = 6 * 60 * 60


def _work_task_counter_key(agent_id: str) -> str:
    return f"pa:work_tasks:{agent_id}:active"


def _quantize_cost_value(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return decimal_value.quantize(_COST_PRECISION)


def _extract_message_content(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _tool_calls_from_content(message: Any) -> list[dict]:
    content = None
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    tool_calls: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if not isinstance(part_type, str):
            continue
        part_type = part_type.lower()
        if part_type not in {"tool_use", "tool_call"}:
            continue
        name = part.get("name") or part.get("tool_name")
        raw_input = part.get("input", part.get("arguments"))
        if raw_input is None:
            raw_input = {}
        if isinstance(raw_input, str):
            arguments = raw_input
        else:
            try:
                arguments = json.dumps(raw_input)
            except Exception:
                arguments = str(raw_input)
        tool_calls.append(
            {
                "id": part.get("id") or part.get("tool_use_id") or f"tool_use_{len(tool_calls)}",
                "type": "function",
                "function": {"name": name or "", "arguments": arguments},
            }
        )
    return tool_calls


def _coerce_tool_call(call: Any, fallback_id: str) -> dict:
    if isinstance(call, dict):
        return call
    function = getattr(call, "function", None)
    call_id = getattr(call, "id", None) or fallback_id
    if function is not None:
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        if arguments is None:
            arguments = ""
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name or "", "arguments": arguments},
        }
    name = getattr(call, "name", None)
    arguments = getattr(call, "arguments", None)
    if arguments is None:
        arguments = ""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name or "", "arguments": arguments},
    }


def _normalize_tool_calls(message: Any) -> list[Any]:
    if message is None:
        return []
    raw_tool_calls = None
    if isinstance(message, dict):
        raw_tool_calls = message.get("tool_calls")
    else:
        raw_tool_calls = getattr(message, "tool_calls", None)
    if raw_tool_calls:
        if isinstance(raw_tool_calls, str):
            try:
                raw_tool_calls = json.loads(raw_tool_calls)
            except Exception:
                return [_coerce_tool_call(raw_tool_calls, "tool_use_0")]
        if isinstance(raw_tool_calls, dict):
            return [_coerce_tool_call(raw_tool_calls, "tool_use_0")]
        if isinstance(raw_tool_calls, list):
            return [
                _coerce_tool_call(call, f"tool_use_{idx}")
                for idx, call in enumerate(raw_tool_calls)
            ]
        try:
            return [
                _coerce_tool_call(call, f"tool_use_{idx}")
                for idx, call in enumerate(list(raw_tool_calls))
            ]
        except TypeError:
            return [_coerce_tool_call(raw_tool_calls, "tool_use_0")]
    return _tool_calls_from_content(message)


def _sanitize_tool_name(name: str) -> str:
    if not name:
        return name
    paren_idx = name.find("(")
    if paren_idx > 0:
        return name[:paren_idx].strip()
    return name


def _get_tool_call_name(call: Any) -> Optional[str]:
    if call is None:
        return None
    function = getattr(call, "function", None)
    if function is not None:
        name = getattr(function, "name", None)
        if name:
            return _sanitize_tool_name(name)
    if isinstance(call, dict):
        function = call.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if name:
                return _sanitize_tool_name(name)
        name = call.get("name")
        if name:
            return _sanitize_tool_name(name)
    name = getattr(call, "name", None)
    if name:
        return _sanitize_tool_name(name)
    return None


def _get_tool_call_arguments(call: Any) -> Any:
    function = getattr(call, "function", None)
    if function is not None:
        args = getattr(function, "arguments", None)
        if args is not None:
            return args
    if isinstance(call, dict):
        function = call.get("function")
        if isinstance(function, dict) and "arguments" in function:
            return function.get("arguments")
        return call.get("arguments")
    return None


def _assistant_message_from_tool_calls(message: Any, tool_calls: list[Any]) -> Dict[str, Any]:
    content = _extract_message_content(message)
    assistant_message: Dict[str, Any] = {"role": "assistant"}
    if content:
        assistant_message["content"] = content
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls
    return assistant_message


def _build_tool_definitions(allowed_tools: list[str]) -> list[dict[str, Any]]:
    manager = get_mcp_manager()
    definitions: list[dict[str, Any]] = []
    for tool_name in allowed_tools:
        info = manager.resolve_tool_info(tool_name)
        if not info:
            continue
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": info.full_name,
                    "description": info.description,
                    "parameters": info.parameters or {"type": "object", "properties": {}},
                },
            }
        )
    return definitions


def _build_system_prompt(allowed_tools: list[str], output_format: Optional[str]) -> str:
    format_hint = ""
    if output_format:
        format_hint = f"Preferred output format: {output_format}. "
    return (
        "You are a stateless work-task agent. Use only the provided tools when needed. "
        "Never claim to have visited sources you did not call tools for. "
        "When you have enough information, respond with a single JSON object matching this schema:\n"
        "{\n  \"summary\": \"...\",\n  \"citations\": [{\"url\": \"...\", \"title\": \"...\", \"note\": \"...\"}]\n}\n"
        "Citations must include explicit URLs from tool results."
        f" {format_hint}Allowed tools: {', '.join(allowed_tools)}."
    )


def _schedule_agent_follow_up(
    task_obj: WorkTask,
    *,
    budget_id: str | None,
    branch_id: str | None,
    depth: int | None,
) -> None:
    agent = getattr(task_obj, "agent", None)
    if agent is None:
        return

    agent_id = str(agent.id)
    try:
        from api.agent.tasks.process_events import process_agent_events_task
    except Exception as exc:  # pragma: no cover - defensive import guard
        logger.error(
            "Unable to import process_agent_events_task for agent %s: %s",
            agent_id,
            exc,
        )
        return

    status = None
    active_id = None
    if budget_id:
        try:
            status = AgentBudgetManager.get_cycle_status(agent_id=agent_id)
            active_id = AgentBudgetManager.get_active_budget_id(agent_id=agent_id)
        except Exception:
            logger.warning(
                "Failed reading budget status for agent %s; scheduling fresh follow-up",
                agent_id,
                exc_info=True,
            )

    try:
        if budget_id and status == "active" and active_id == budget_id:
            parent_depth = max((depth or 1) - 1, 0)
            process_agent_events_task.delay(
                agent_id,
                budget_id=budget_id,
                branch_id=branch_id,
                depth=parent_depth,
                eval_run_id=getattr(task_obj, "eval_run_id", None),
            )
        else:
            process_agent_events_task.delay(agent_id, eval_run_id=getattr(task_obj, "eval_run_id", None))
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to trigger agent event processing for work task %s: %s", task_obj.id, exc)


def _decrement_counter_and_maybe_follow_up(task_obj: WorkTask, *, budget_id: str | None, branch_id: str | None, depth: int | None) -> None:
    agent = getattr(task_obj, "agent", None)
    if agent is None:
        return

    remaining: Optional[int] = None
    try:
        redis_client = get_redis_client()
        if redis_client:
            key = _work_task_counter_key(str(agent.id))
            remaining = int(redis_client.decr(key))
            if remaining < 0:
                redis_client.set(key, 0)
                remaining = 0
            redis_client.expire(key, _WORK_TASK_REDIS_TTL_SECONDS)
    except Exception:
        logger.debug("Failed to decrement work task counter", exc_info=True)

    if remaining is None:
        remaining = WorkTask.objects.filter(
            agent=agent,
            status__in=[WorkTask.StatusChoices.PENDING, WorkTask.StatusChoices.IN_PROGRESS],
        ).count()

    if remaining == 0:
        _schedule_agent_follow_up(task_obj, budget_id=budget_id, branch_id=branch_id, depth=depth)


@shared_task
def process_work_task(
    work_task_id: str,
    *,
    allowed_tools: Optional[List[str]] = None,
    output_format: Optional[str] = None,
    max_steps: Optional[int] = None,
    budget_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    depth: Optional[int] = None,
) -> None:
    close_old_connections()

    try:
        task_obj = WorkTask.objects.select_related("agent").get(id=work_task_id)
    except WorkTask.DoesNotExist:
        logger.error("WorkTask %s not found", work_task_id)
        return

    settings = get_work_task_settings()
    max_steps = max_steps or settings.max_steps
    if max_steps <= 0:
        max_steps = settings.max_steps

    tool_limit = settings.max_tool_calls
    allowed_tools = allowed_tools or list(WORK_TASK_ALLOWED_MCP_TOOLS)
    allowed_tools = [tool for tool in allowed_tools if tool in WORK_TASK_ALLOWED_MCP_TOOLS]

    llm_config = get_work_task_llm_config()
    failure_message: Optional[str] = None
    tool_definitions: list[dict[str, Any]] = []
    messages: List[Dict[str, Any]] = []
    if llm_config is None:
        failure_message = "Work task LLM configuration missing."
    else:
        tool_definitions = _build_tool_definitions(allowed_tools)
        if not tool_definitions:
            failure_message = "No MCP tool definitions available for work tasks."
        else:
            system_prompt = _build_system_prompt(allowed_tools, output_format)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task_obj.query or ""},
            ]

    if not failure_message:
        task_obj.status = WorkTask.StatusChoices.IN_PROGRESS
        task_obj.updated_at = timezone.now()
        try:
            task_obj.save(update_fields=["status", "updated_at"])
        except OperationalError:
            close_old_connections()
            task_obj.save(update_fields=["status", "updated_at"])

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_cached_tokens = 0
    total_input_cost = Decimal("0")
    total_input_uncached = Decimal("0")
    total_input_cached = Decimal("0")
    total_output_cost = Decimal("0")
    total_cost = Decimal("0")
    tool_call_count = 0
    summary_payload: Optional[Dict[str, Any]] = None

    try:
        if not failure_message and llm_config is not None:
            for _ in range(1, max_steps + 1):
                response = run_completion(
                    model=llm_config.model,
                    messages=messages,
                    params=llm_config.params,
                    tools=tool_definitions,
                )

                message = None
                if isinstance(response, dict):
                    choices = response.get("choices") or []
                    message = choices[0].get("message") if choices else None
                else:
                    message = getattr(response.choices[0], "message", None) if getattr(response, "choices", None) else None

                token_usage, raw_usage = extract_token_usage(response, model=llm_config.model)
                if token_usage:
                    total_prompt_tokens += int(token_usage.get("prompt_tokens") or 0)
                    total_completion_tokens += int(token_usage.get("completion_tokens") or 0)
                    total_tokens += int(token_usage.get("total_tokens") or 0)
                    total_cached_tokens += int(token_usage.get("cached_tokens") or 0)
                    cost_fields = compute_cost_breakdown(token_usage, raw_usage)
                    total_input_cost += _quantize_cost_value(cost_fields.get("input_cost_total")) or Decimal("0")
                    total_input_uncached += _quantize_cost_value(cost_fields.get("input_cost_uncached")) or Decimal("0")
                    total_input_cached += _quantize_cost_value(cost_fields.get("input_cost_cached")) or Decimal("0")
                    total_output_cost += _quantize_cost_value(cost_fields.get("output_cost")) or Decimal("0")
                    total_cost += _quantize_cost_value(cost_fields.get("total_cost")) or Decimal("0")

                tool_calls = _normalize_tool_calls(message)
                if tool_calls:
                    messages.append(_assistant_message_from_tool_calls(message, tool_calls))
                    for tool_call in tool_calls:
                        tool_call_count += 1
                        if tool_limit and tool_call_count > tool_limit:
                            failure_message = f"Maximum tool call limit reached ({tool_limit})."
                            break

                        tool_name = _get_tool_call_name(tool_call) or ""
                        tool_params: Dict[str, Any] = {}
                        if tool_name not in allowed_tools:
                            tool_result = {
                                "status": "error",
                                "message": f"Tool '{tool_name}' is not allowed for work tasks.",
                            }
                        else:
                            raw_args = _get_tool_call_arguments(tool_call)
                            try:
                                tool_params = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                            except Exception as exc:
                                tool_result = {
                                    "status": "error",
                                    "message": f"Invalid tool arguments: {exc}",
                                }
                            else:
                                server_name = extract_mcp_server_name(tool_name)
                                tool_result = execute_platform_mcp_tool(server_name, tool_name, tool_params)

                        WorkTaskStep.objects.create(
                            task=task_obj,
                            step_number=tool_call_count,
                            tool_name=tool_name or "unknown",
                            tool_params=tool_params if isinstance(tool_params, dict) else {},
                            tool_result=tool_result,
                        )

                        tool_call_id = None
                        if isinstance(tool_call, dict):
                            tool_call_id = tool_call.get("id")
                        if not tool_call_id:
                            tool_call_id = f"tool_{tool_call_count}"

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": json.dumps(tool_result, default=str),
                            }
                        )

                    if failure_message:
                        break
                    continue

                summary_text = _extract_message_content(message)
                if summary_text.strip():
                    summary_payload = coerce_summary_payload(summary_text)
                    break
                failure_message = "Model response was empty."
                break

            if summary_payload is None and not failure_message:
                failure_message = f"Maximum step limit reached ({max_steps}) without completion."

    except Exception as exc:
        logger.exception("WorkTask %s failed during execution", task_obj.id)
        failure_message = f"Work task execution failed: {exc}"

    finally:
        try:
            if branch_id and task_obj.agent:
                AgentBudgetManager.bump_branch_depth(
                    agent_id=str(task_obj.agent.id),
                    branch_id=str(branch_id),
                    delta=-1,
                )
        except Exception:
            logger.warning("Failed to decrement outstanding work-task children", exc_info=True)

        close_old_connections()
        task_obj.prompt_tokens = total_prompt_tokens or None
        task_obj.completion_tokens = total_completion_tokens or None
        task_obj.total_tokens = total_tokens or None
        task_obj.cached_tokens = total_cached_tokens or None
        task_obj.input_cost_total = total_input_cost or None
        task_obj.input_cost_uncached = total_input_uncached or None
        task_obj.input_cost_cached = total_input_cached or None
        task_obj.output_cost = total_output_cost or None
        task_obj.total_cost = total_cost or None
        llm_model_value = llm_config.model if llm_config else None
        task_obj.llm_model = llm_model_value
        task_obj.llm_provider = (
            llm_model_value.split("/", 1)[0]
            if llm_model_value and "/" in llm_model_value
            else None
        )

        if summary_payload is not None:
            task_obj.status = WorkTask.StatusChoices.COMPLETED
            task_obj.result_summary = json.dumps(summary_payload)
            task_obj.error_message = None
        else:
            task_obj.status = WorkTask.StatusChoices.FAILED
            task_obj.error_message = failure_message

        task_obj.updated_at = timezone.now()
        try:
            task_obj.save(
                update_fields=[
                    "status",
                    "result_summary",
                    "error_message",
                    "updated_at",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cached_tokens",
                    "llm_model",
                    "llm_provider",
                    "input_cost_total",
                    "input_cost_uncached",
                    "input_cost_cached",
                    "output_cost",
                    "total_cost",
                ]
            )
        except OperationalError:
            close_old_connections()
            task_obj.save(
                update_fields=[
                    "status",
                    "result_summary",
                    "error_message",
                    "updated_at",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cached_tokens",
                    "llm_model",
                    "llm_provider",
                    "input_cost_total",
                    "input_cost_uncached",
                    "input_cost_cached",
                    "output_cost",
                    "total_cost",
                ]
            )

        tool_status = "success" if task_obj.status == WorkTask.StatusChoices.COMPLETED else "error"
        if task_obj.tool_call_step_id:
            try:
                from api.models import PersistentAgentToolCall

                summary_payload = coerce_summary_payload(task_obj.result_summary or "") if task_obj.result_summary else None
                tool_payload = build_work_task_tool_result(
                    task_id=str(task_obj.id),
                    status=tool_status,
                    result_summary=summary_payload,
                    error_message=task_obj.error_message or None,
                )
                PersistentAgentToolCall.objects.filter(step_id=task_obj.tool_call_step_id).update(
                    result=serialize_tool_result(tool_payload)
                )
            except Exception:
                logger.debug("Failed to update tool call result preview for work task %s", task_obj.id, exc_info=True)

        props = Analytics.with_org_properties(
            {
                "agent_id": str(getattr(task_obj.agent, "id", "")) if task_obj.agent else None,
                "task_id": str(task_obj.id),
                "status": task_obj.status,
                "model": task_obj.llm_model,
                "provider": task_obj.llm_provider,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_tokens,
                "total_cost": str(task_obj.total_cost) if task_obj.total_cost is not None else None,
                "tool_count": tool_call_count,
            },
            organization=getattr(task_obj.agent, "organization", None) if task_obj.agent else None,
        )

        try:
            if task_obj.status == WorkTask.StatusChoices.COMPLETED:
                event = AnalyticsEvent.PERSISTENT_AGENT_WORK_TASK_COMPLETED
            elif task_obj.status == WorkTask.StatusChoices.FAILED:
                event = AnalyticsEvent.PERSISTENT_AGENT_WORK_TASK_FAILED
            else:
                event = AnalyticsEvent.PERSISTENT_AGENT_WORK_TASK_CANCELLED
            Analytics.track_event(
                task_obj.user_id,
                event,
                AnalyticsSource.AGENT,
                props,
            )
        except Exception:
            logger.debug("Failed to emit analytics for work task completion", exc_info=True)

        _decrement_counter_and_maybe_follow_up(task_obj, budget_id=budget_id, branch_id=branch_id, depth=depth)
