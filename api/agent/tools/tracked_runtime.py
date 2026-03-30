import json
import logging
import time
from decimal import Decimal
from typing import Any, Dict, Optional

from api.agent.comms.human_input_requests import (
    attach_originating_step_from_result,
    track_human_input_request_created,
)
from api.models import PersistentAgent, PersistentAgentStep

from .runtime_execution_context import tool_execution_context
from .tool_runtime import execute_runtime_tool_call

logger = logging.getLogger(__name__)


def _build_attach_completion(parent_step: Optional[PersistentAgentStep]):
    parent_completion = None
    parent_eval_run = None
    if parent_step is not None:
        if getattr(parent_step, "completion_id", None):
            parent_completion = getattr(parent_step, "completion", None)
        if getattr(parent_step, "eval_run_id", None):
            parent_eval_run = getattr(parent_step, "eval_run", None)

    def _attach(step_kwargs: Dict[str, Any]) -> None:
        if parent_completion is not None:
            step_kwargs["completion"] = parent_completion
        if parent_eval_run is not None:
            step_kwargs["eval_run"] = parent_eval_run

    return _attach


def _no_prompt_archive(_step: PersistentAgentStep) -> None:
    return None


def execute_tracked_runtime_tool_call(
    agent: PersistentAgent,
    *,
    tool_name: str,
    exec_params: Dict[str, Any],
    parent_step: Optional[PersistentAgentStep] = None,
    isolated_mcp: bool = False,
) -> tuple[Any, Optional[list[dict]]]:
    from api.agent.core.event_processing import (
        _build_safe_error_payload,
        _create_pending_tool_call_step,
        _enforce_tool_rate_limit,
        _ensure_credit_for_tool,
        _finalize_pending_tool_call_step,
        _is_error_status,
        _normalize_error_result,
        _persist_tool_call_step,
    )

    attach_completion = _build_attach_completion(parent_step)

    if not _enforce_tool_rate_limit(
        agent,
        tool_name,
        attach_completion=attach_completion,
        attach_prompt_archive=_no_prompt_archive,
    ):
        return {
            "status": "error",
            "message": f"Tool '{tool_name}' skipped due to hourly rate limit.",
        }, None

    credit_info = _ensure_credit_for_tool(agent, tool_name)
    if not credit_info:
        return {
            "status": "error",
            "message": f"Tool '{tool_name}' skipped due to credit limits.",
        }, None

    credits_consumed = credit_info.get("cost")
    consumed_credit = credit_info.get("credit")
    pending_step = _create_pending_tool_call_step(
        agent=agent,
        tool_name=tool_name,
        tool_params=exec_params,
        credits_consumed=credits_consumed,
        consumed_credit=consumed_credit,
        attach_completion=attach_completion,
        attach_prompt_archive=_no_prompt_archive,
    )

    result = None
    updated_tools: Optional[list[dict]] = None
    duration_ms: Optional[int] = None
    try:
        started_at = time.monotonic()
        context_step_id = str(pending_step.id) if pending_step is not None else None
        with tool_execution_context(step_id=context_step_id):
            result, updated_tools = execute_runtime_tool_call(
                agent,
                tool_name=tool_name,
                exec_params=exec_params,
                isolated_mcp=isolated_mcp,
            )
        duration_ms = int(round((time.monotonic() - started_at) * 1000))
    except Exception as exc:
        logger.exception(
            "Tracked runtime tool call failed for agent %s tool %s",
            getattr(agent, "id", None),
            tool_name,
        )
        result = _build_safe_error_payload(
            f"Tool execution failed: {exc}",
            error_type=type(exc).__name__,
            retryable=False,
        )

    if _is_error_status(result):
        result = _normalize_error_result(result)
    tool_status = "error" if _is_error_status(result) else "complete"

    try:
        result_content = json.dumps(result)
    except (TypeError, ValueError):
        result_content = json.dumps(result, default=str)

    if pending_step is not None:
        _finalize_pending_tool_call_step(
            step=pending_step,
            tool_name=tool_name,
            tool_params=exec_params,
            result_content=result_content,
            execution_duration_ms=duration_ms,
            status=tool_status,
        )
        step = pending_step
    else:
        step = _persist_tool_call_step(
            agent=agent,
            tool_name=tool_name,
            tool_params=exec_params,
            result_content=result_content,
            execution_duration_ms=duration_ms,
            status=tool_status,
            credits_consumed=credits_consumed if isinstance(credits_consumed, Decimal) else credits_consumed,
            consumed_credit=consumed_credit,
            attach_completion=attach_completion,
            attach_prompt_archive=_no_prompt_archive,
        )

    if step is not None and tool_name == "request_human_input" and isinstance(result, dict):
        attach_originating_step_from_result(step, result)
        track_human_input_request_created(step, result)

    return result, updated_tools
