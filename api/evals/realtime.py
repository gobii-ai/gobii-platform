import logging
from typing import Any, Dict, Iterable, List, Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from api.models import EvalRun, EvalRunTask, EvalSuiteRun

logger = logging.getLogger(__name__)


def _send(group: str, message_type: str, payload: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(group, {"type": message_type, "payload": payload})
    except Exception:
        logger.exception("Failed to broadcast %s to group %s", message_type, group)


def _serialize_task(task: EvalRunTask) -> dict:
    return {
        "id": task.id,
        "run_id": str(task.run_id),
        "sequence": task.sequence,
        "name": task.name,
        "status": task.status,
        "assertion_type": task.assertion_type,
        "expected_summary": task.expected_summary,
        "observed_summary": task.observed_summary,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "prompt_tokens": task.prompt_tokens,
        "completion_tokens": task.completion_tokens,
        "total_tokens": task.total_tokens,
        "cached_tokens": task.cached_tokens,
        "input_cost_total": float(task.input_cost_total),
        "input_cost_uncached": float(task.input_cost_uncached),
        "input_cost_cached": float(task.input_cost_cached),
        "output_cost": float(task.output_cost),
        "total_cost": float(task.total_cost),
        "credits_cost": float(task.credits_cost),
    }


def _serialize_run(run: EvalRun, *, include_tasks: bool = False, tasks: Optional[Iterable[EvalRunTask]] = None) -> dict:
    payload = {
        "id": str(run.id),
        "suite_run_id": str(run.suite_run_id) if run.suite_run_id else None,
        "scenario_slug": run.scenario_slug,
        "scenario_version": run.scenario_version,
        "status": run.status,
        "run_type": run.run_type,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "agent_id": str(run.agent_id) if run.agent_id else None,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "cached_tokens": run.cached_tokens,
        "tokens_used": run.tokens_used,
        "input_cost_total": float(run.input_cost_total),
        "input_cost_uncached": float(run.input_cost_uncached),
        "input_cost_cached": float(run.input_cost_cached),
        "output_cost": float(run.output_cost),
        "total_cost": float(run.total_cost),
        "credits_cost": float(run.credits_cost),
        "completion_count": run.completion_count,
        "step_count": run.step_count,
    }

    if include_tasks:
        tasks_iterable = tasks if tasks is not None else run.tasks.all()
        payload["tasks"] = [_serialize_task(task) for task in tasks_iterable]

    return payload


def _serialize_suite(suite: EvalSuiteRun, *, include_runs: bool = False, runs: Optional[Iterable[EvalRun]] = None) -> dict:
    payload = {
        "id": str(suite.id),
        "suite_slug": suite.suite_slug,
        "status": suite.status,
        "run_type": suite.run_type,
        "requested_runs": suite.requested_runs,
        "agent_strategy": suite.agent_strategy,
        "shared_agent_id": str(suite.shared_agent_id) if suite.shared_agent_id else None,
        "started_at": suite.started_at.isoformat() if suite.started_at else None,
        "finished_at": suite.finished_at.isoformat() if suite.finished_at else None,
    }

    if include_runs:
        runs_iterable = runs if runs is not None else suite.runs.all()
        run_list = list(runs_iterable)
        payload["runs"] = [_serialize_run(run, include_tasks=False) for run in run_list]
        payload["cost_totals"] = {
            "prompt_tokens": sum(r.prompt_tokens for r in run_list),
            "completion_tokens": sum(r.completion_tokens for r in run_list),
            "cached_tokens": sum(r.cached_tokens for r in run_list),
            "tokens_used": sum(r.tokens_used for r in run_list),
            "input_cost_total": float(sum(r.input_cost_total for r in run_list)),
            "input_cost_uncached": float(sum(r.input_cost_uncached for r in run_list)),
            "input_cost_cached": float(sum(r.input_cost_cached for r in run_list)),
            "output_cost": float(sum(r.output_cost for r in run_list)),
            "total_cost": float(sum(r.total_cost for r in run_list)),
            "credits_cost": float(sum(r.credits_cost for r in run_list)),
        }

    return payload


def broadcast_suite_update(suite: EvalSuiteRun, *, include_runs: bool = False) -> None:
    payload = _serialize_suite(suite, include_runs=include_runs)
    _send(f"eval-suite-{suite.id}", "suite.update", payload)


def broadcast_run_update(run: EvalRun, *, include_tasks: bool = False, tasks: Optional[List[EvalRunTask]] = None) -> None:
    payload = _serialize_run(run, include_tasks=include_tasks, tasks=tasks)
    _send(f"eval-run-{run.id}", "run.update", payload)

    # Bubble up to suite listeners
    if run.suite_run_id:
        _send(
            f"eval-suite-{run.suite_run_id}",
            "run.update",
            payload,
        )


def broadcast_task_update(task: EvalRunTask) -> None:
    payload = _serialize_task(task)
    _send(f"eval-run-{task.run_id}", "task.update", payload)

    if task.run.suite_run_id:
        _send(f"eval-suite-{task.run.suite_run_id}", "task.update", payload)
