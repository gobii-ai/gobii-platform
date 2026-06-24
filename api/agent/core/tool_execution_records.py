import json
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from django.db import DatabaseError, close_old_connections
from django.db.utils import OperationalError

from api.services.agent_error_logging import log_tool_persistence_error

logger = logging.getLogger(__name__)


def normalize_tool_result_content(raw: str) -> str:
    """Decode stringified JSON payloads so nested arrays/objects stay structured."""
    from api.agent.tools.json_utils import decode_embedded_json_strings

    if not raw or not isinstance(raw, str):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(parsed, (dict, list)):
        return raw
    normalized = decode_embedded_json_strings(parsed)
    try:
        return json.dumps(normalized, ensure_ascii=False)
    except TypeError:
        return raw


def _build_tool_call_description(
    tool_name: str,
    tool_params: Dict[str, Any],
    normalized_result: str | None,
) -> str:
    safe_tool_name = (tool_name or "")[:256]
    try:
        params_preview = str(tool_params)[:100] if tool_params else ""
        result_preview = (normalized_result or "")[:100]
        return f"Tool call: {safe_tool_name}({params_preview}) -> {result_preview}"
    except Exception:
        return f"Tool call: {safe_tool_name}"


def _emit_tool_call_realtime(step: Any, context: str) -> None:
    try:
        from console.agent_chat.signals import emit_tool_call_realtime

        emit_tool_call_realtime(step)
    except Exception:
        logger.debug(
            "Failed to broadcast %s tool call for agent %s step %s",
            context,
            getattr(step, "agent_id", None),
            getattr(step, "id", None),
            exc_info=True,
        )


def _emit_tool_call_audit(step: Any, context: str) -> None:
    try:
        from console.agent_chat.signals import emit_tool_call_audit

        emit_tool_call_audit(step)
    except Exception:
        logger.debug(
            "Failed to broadcast %s tool call audit for agent %s step %s",
            context,
            getattr(step, "agent_id", None),
            getattr(step, "id", None),
            exc_info=True,
        )


def _tool_context_for_error(
    tool_name: str,
    tool_params: Dict[str, Any] | None,
    *,
    result_content: str | None = None,
    execution_duration_ms: Optional[int] = None,
    status: str | None = None,
    credits_consumed: Any = None,
    consumed_credit: Any = None,
    step: Any = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "tool_name": tool_name,
        "tool_status": status,
        "execution_duration_ms": execution_duration_ms,
        "param_keys": sorted(str(key) for key in tool_params.keys()) if isinstance(tool_params, dict) else [],
        "result_length": len(result_content or ""),
        "credits_consumed": str(credits_consumed) if credits_consumed is not None else None,
        "task_credit_id": str(getattr(consumed_credit, "id", "")) if consumed_credit is not None else None,
    }
    if step is not None:
        context["step_id"] = str(getattr(step, "id", ""))
        context["completion_id"] = str(getattr(step, "completion_id", "")) if getattr(step, "completion_id", None) else None
    return context


def _completion_from_step_kwargs(step_kwargs: dict[str, Any]) -> Any:
    from api.models import PersistentAgentCompletion

    completion = step_kwargs.get("completion")
    return completion if isinstance(completion, PersistentAgentCompletion) else None


def _agent_from_step(step: Any) -> Any:
    from api.models import PersistentAgent

    try:
        return step.agent
    except (AttributeError, DatabaseError, PersistentAgent.DoesNotExist):
        return None


def persist_tool_call_step(
    agent: Any,
    tool_name: str,
    tool_params: Dict[str, Any],
    result_content: str,
    execution_duration_ms: Optional[int],
    status: str | None,
    credits_consumed: Any,
    consumed_credit: Any,
    attach_completion: Any,
    attach_prompt_archive: Any,
    parent_tool_call: Any = None,
) -> Any:
    """Persist a completed tool call without letting persistence failures stop execution."""
    from api.models import PersistentAgentStep, PersistentAgentToolCall

    normalized_result = normalize_tool_result_content(result_content)
    safe_tool_name = (tool_name or "")[:256]
    description = _build_tool_call_description(safe_tool_name, tool_params, normalized_result)
    step_kwargs = {
        "agent": agent,
        "description": description[:500],
        "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
        "task_credit": consumed_credit,
    }

    def _try_create_step() -> Optional[PersistentAgentStep]:
        attach_completion(step_kwargs)
        step = PersistentAgentStep.objects.create(**step_kwargs)
        attach_prompt_archive(step)
        PersistentAgentToolCall.objects.create(
            step=step,
            parent_tool_call=parent_tool_call,
            tool_name=safe_tool_name,
            tool_params=tool_params,
            result=normalized_result,
            execution_duration_ms=execution_duration_ms,
            status=status or "complete",
        )
        _emit_tool_call_realtime(step, "realtime")
        return step

    try:
        step = _try_create_step()
        logger.info(
            "Agent %s: persisted tool call step_id=%s for %s",
            agent.id,
            getattr(step, "id", None),
            safe_tool_name,
        )
        return step
    except OperationalError:
        close_old_connections()
        try:
            step = _try_create_step()
            logger.info(
                "Agent %s: persisted tool call (retry) step_id=%s for %s",
                agent.id,
                getattr(step, "id", None),
                safe_tool_name,
            )
            return step
        except Exception as retry_exc:
            log_tool_persistence_error(
                agent,
                retry_exc,
                source="api.agent.core.tool_execution_records.persist_tool_call_step.retry",
                logger=logger,
                completion=_completion_from_step_kwargs(step_kwargs),
                context=_tool_context_for_error(
                    safe_tool_name,
                    tool_params,
                    result_content=normalized_result,
                    execution_duration_ms=execution_duration_ms,
                    status=status or "complete",
                    credits_consumed=credits_consumed,
                    consumed_credit=consumed_credit,
                ),
            )
            return None
    except DatabaseError as db_exc:
        log_tool_persistence_error(
            agent,
            db_exc,
            source="api.agent.core.tool_execution_records.persist_tool_call_step",
            logger=logger,
            completion=_completion_from_step_kwargs(step_kwargs),
            context=_tool_context_for_error(
                safe_tool_name,
                tool_params,
                result_content=normalized_result,
                execution_duration_ms=execution_duration_ms,
                status=status or "complete",
                credits_consumed=credits_consumed,
                consumed_credit=consumed_credit,
            ),
        )
        return None
    except Exception as exc:
        log_tool_persistence_error(
            agent,
            exc,
            source="api.agent.core.tool_execution_records.persist_tool_call_step",
            logger=logger,
            completion=_completion_from_step_kwargs(step_kwargs),
            context=_tool_context_for_error(
                safe_tool_name,
                tool_params,
                result_content=normalized_result,
                execution_duration_ms=execution_duration_ms,
                status=status or "complete",
                credits_consumed=credits_consumed,
                consumed_credit=consumed_credit,
            ),
        )
        return None


def create_pending_tool_call_step(
    agent: Any,
    tool_name: str,
    tool_params: Dict[str, Any],
    credits_consumed: Any,
    consumed_credit: Any,
    attach_completion: Any,
    attach_prompt_archive: Any,
    parent_tool_call: Any = None,
) -> Any:
    from api.models import PersistentAgentStep, PersistentAgentToolCall

    safe_tool_name = (tool_name or "")[:256]
    step_kwargs = {
        "agent": agent,
        "description": "",
        "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
        "task_credit": consumed_credit,
    }

    try:
        attach_completion(step_kwargs)
        step = PersistentAgentStep.objects.create(**step_kwargs)
        attach_prompt_archive(step)
        PersistentAgentToolCall.objects.create(
            step=step,
            parent_tool_call=parent_tool_call,
            tool_name=safe_tool_name,
            tool_params=tool_params,
            result="",
            execution_duration_ms=None,
            status="pending",
        )
        _emit_tool_call_realtime(step, "pending")
        return step
    except Exception as exc:
        log_tool_persistence_error(
            agent,
            exc,
            source="api.agent.core.tool_execution_records.create_pending_tool_call_step",
            logger=logger,
            completion=_completion_from_step_kwargs(step_kwargs),
            context=_tool_context_for_error(
                safe_tool_name,
                tool_params,
                status="pending",
                credits_consumed=credits_consumed,
                consumed_credit=consumed_credit,
            ),
        )
        return None


def finalize_pending_tool_call_step(
    step: Any,
    tool_name: str,
    tool_params: Dict[str, Any],
    result_content: str,
    execution_duration_ms: Optional[int],
    status: str,
    parent_tool_call: Any = None,
) -> None:
    from api.models import PersistentAgentToolCall

    normalized_result = normalize_tool_result_content(result_content)
    safe_tool_name = (tool_name or "")[:256]
    description = _build_tool_call_description(safe_tool_name, tool_params, normalized_result)

    try:
        step.description = description[:500]
        step.save(update_fields=["description"])
    except Exception as exc:
        agent = _agent_from_step(step)
        if agent is not None:
            log_tool_persistence_error(
                agent,
                exc,
                source="api.agent.core.tool_execution_records.finalize_pending_tool_call_step.description",
                logger=logger,
                context=_tool_context_for_error(
                    safe_tool_name,
                    tool_params,
                    result_content=normalized_result,
                    execution_duration_ms=execution_duration_ms,
                    status=status,
                    step=step,
                ),
            )
        else:
            logger.debug(
                "Failed to update tool step description for agent %s step %s",
                getattr(step, "agent_id", None),
                getattr(step, "id", None),
                exc_info=True,
            )

    created_tool_call = False
    try:
        tool_call = getattr(step, "tool_call", None)
        if tool_call is None:
            tool_call = PersistentAgentToolCall.objects.create(
                step=step,
                parent_tool_call=parent_tool_call,
                tool_name=safe_tool_name,
                tool_params=tool_params,
                result=normalized_result,
                execution_duration_ms=execution_duration_ms,
                status=status,
            )
            created_tool_call = True
        else:
            tool_call.tool_name = safe_tool_name
            tool_call.tool_params = tool_params
            tool_call.result = normalized_result
            tool_call.execution_duration_ms = execution_duration_ms
            tool_call.status = status
            update_fields = ["tool_name", "tool_params", "result", "execution_duration_ms", "status"]
            if parent_tool_call is not None and tool_call.parent_tool_call_id is None:
                tool_call.parent_tool_call = parent_tool_call
                update_fields.append("parent_tool_call")
            tool_call.save(update_fields=update_fields)
    except Exception as exc:
        agent = _agent_from_step(step)
        if agent is not None:
            log_tool_persistence_error(
                agent,
                exc,
                source="api.agent.core.tool_execution_records.finalize_pending_tool_call_step",
                logger=logger,
                context=_tool_context_for_error(
                    safe_tool_name,
                    tool_params,
                    result_content=normalized_result,
                    execution_duration_ms=execution_duration_ms,
                    status=status,
                    step=step,
                ),
            )
        else:
            logger.debug(
                "Failed to finalize tool call for agent %s step %s",
                getattr(step, "agent_id", None),
                getattr(step, "id", None),
                exc_info=True,
            )
        return

    _emit_tool_call_realtime(step, "finalized")
    if not created_tool_call:
        _emit_tool_call_audit(step, "finalized")
