import logging
import traceback
from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from api.models import (
    BrowserUseAgentTask,
    EvalRun,
    EvalRunTask,
    EvalSuiteRun,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentStep,
)
from api.evals.registry import ScenarioRegistry
from api.evals.realtime import broadcast_run_update, broadcast_suite_update, broadcast_task_update
import api.evals.loader # noqa: F401

logger = logging.getLogger(__name__)


def _decimal_zero(output_digits: int = 20, output_places: int = 6) -> Value:
    return Value(Decimal("0"), output_field=DecimalField(max_digits=output_digits, decimal_places=output_places))


def _aggregate_eval_run_metrics(run: EvalRun) -> None:
    """Populate EvalRun and EvalRunTask cost/token rollups from tagged usage rows."""

    dec_zero = _decimal_zero()
    int_zero = Value(0)

    completions_qs = PersistentAgentCompletion.objects.filter(eval_run_id=run.id)
    steps_qs = PersistentAgentStep.objects.filter(eval_run_id=run.id)
    browser_qs = BrowserUseAgentTask.objects.filter(eval_run_id=run.id)

    completion_agg = completions_qs.aggregate(
        prompt_tokens=Coalesce(Sum("prompt_tokens"), int_zero),
        completion_tokens=Coalesce(Sum("completion_tokens"), int_zero),
        total_tokens=Coalesce(Sum("total_tokens"), int_zero),
        cached_tokens=Coalesce(Sum("cached_tokens"), int_zero),
        input_cost_total=Coalesce(Sum("input_cost_total"), dec_zero),
        input_cost_uncached=Coalesce(Sum("input_cost_uncached"), dec_zero),
        input_cost_cached=Coalesce(Sum("input_cost_cached"), dec_zero),
        output_cost=Coalesce(Sum("output_cost"), dec_zero),
        total_cost=Coalesce(Sum("total_cost"), dec_zero),
    )

    browser_agg = browser_qs.aggregate(
        prompt_tokens=Coalesce(Sum("prompt_tokens"), int_zero),
        completion_tokens=Coalesce(Sum("completion_tokens"), int_zero),
        total_tokens=Coalesce(Sum("total_tokens"), int_zero),
        cached_tokens=Coalesce(Sum("cached_tokens"), int_zero),
        input_cost_total=Coalesce(Sum("input_cost_total"), dec_zero),
        input_cost_uncached=Coalesce(Sum("input_cost_uncached"), dec_zero),
        input_cost_cached=Coalesce(Sum("input_cost_cached"), dec_zero),
        output_cost=Coalesce(Sum("output_cost"), dec_zero),
        total_cost=Coalesce(Sum("total_cost"), dec_zero),
        credits_cost=Coalesce(Sum("credits_cost"), dec_zero),
    )

    step_agg = steps_qs.aggregate(
        credits_cost=Coalesce(Sum("credits_cost"), dec_zero),
    )

    run.prompt_tokens = int(completion_agg.get("prompt_tokens", 0) + browser_agg.get("prompt_tokens", 0))
    run.completion_tokens = int(
        completion_agg.get("completion_tokens", 0) + browser_agg.get("completion_tokens", 0)
    )
    run.cached_tokens = int(completion_agg.get("cached_tokens", 0) + browser_agg.get("cached_tokens", 0))
    run.tokens_used = int(completion_agg.get("total_tokens", 0) + browser_agg.get("total_tokens", 0))

    run.input_cost_total = completion_agg.get("input_cost_total", Decimal("0")) + browser_agg.get(
        "input_cost_total", Decimal("0")
    )
    run.input_cost_uncached = completion_agg.get("input_cost_uncached", Decimal("0")) + browser_agg.get(
        "input_cost_uncached", Decimal("0")
    )
    run.input_cost_cached = completion_agg.get("input_cost_cached", Decimal("0")) + browser_agg.get(
        "input_cost_cached", Decimal("0")
    )
    run.output_cost = completion_agg.get("output_cost", Decimal("0")) + browser_agg.get(
        "output_cost", Decimal("0")
    )
    run.total_cost = completion_agg.get("total_cost", Decimal("0")) + browser_agg.get(
        "total_cost", Decimal("0")
    )

    run.credits_cost = step_agg.get("credits_cost", Decimal("0")) + browser_agg.get("credits_cost", Decimal("0"))

    run.completion_count = completions_qs.count()
    run.step_count = steps_qs.count()

    run.save(
        update_fields=[
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "tokens_used",
            "input_cost_total",
            "input_cost_uncached",
            "input_cost_cached",
            "output_cost",
            "total_cost",
            "credits_cost",
            "completion_count",
            "step_count",
            "updated_at",
        ]
    )

    # Aggregate per-task windows
    task_update_fields = [
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "input_cost_total",
        "input_cost_uncached",
        "input_cost_cached",
        "output_cost",
        "total_cost",
        "credits_cost",
        "updated_at",
    ]

    for task in run.tasks.all():
        if not (task.started_at and task.finished_at):
            continue

        window = {
            "eval_run_id": run.id,
            "created_at__gte": task.started_at,
            "created_at__lte": task.finished_at,
        }

        task_completion_qs = PersistentAgentCompletion.objects.filter(**window)
        task_browser_qs = BrowserUseAgentTask.objects.filter(**window)
        task_step_qs = PersistentAgentStep.objects.filter(**window)

        task_completion_agg = task_completion_qs.aggregate(
            prompt_tokens=Coalesce(Sum("prompt_tokens"), int_zero),
            completion_tokens=Coalesce(Sum("completion_tokens"), int_zero),
            total_tokens=Coalesce(Sum("total_tokens"), int_zero),
            cached_tokens=Coalesce(Sum("cached_tokens"), int_zero),
            input_cost_total=Coalesce(Sum("input_cost_total"), dec_zero),
            input_cost_uncached=Coalesce(Sum("input_cost_uncached"), dec_zero),
            input_cost_cached=Coalesce(Sum("input_cost_cached"), dec_zero),
            output_cost=Coalesce(Sum("output_cost"), dec_zero),
            total_cost=Coalesce(Sum("total_cost"), dec_zero),
        )

        task_browser_agg = task_browser_qs.aggregate(
            prompt_tokens=Coalesce(Sum("prompt_tokens"), int_zero),
            completion_tokens=Coalesce(Sum("completion_tokens"), int_zero),
            total_tokens=Coalesce(Sum("total_tokens"), int_zero),
            cached_tokens=Coalesce(Sum("cached_tokens"), int_zero),
            input_cost_total=Coalesce(Sum("input_cost_total"), dec_zero),
            input_cost_uncached=Coalesce(Sum("input_cost_uncached"), dec_zero),
            input_cost_cached=Coalesce(Sum("input_cost_cached"), dec_zero),
            output_cost=Coalesce(Sum("output_cost"), dec_zero),
            total_cost=Coalesce(Sum("total_cost"), dec_zero),
            credits_cost=Coalesce(Sum("credits_cost"), dec_zero),
        )

        task_step_agg = task_step_qs.aggregate(
            credits_cost=Coalesce(Sum("credits_cost"), dec_zero),
        )

        task.prompt_tokens = int(task_completion_agg.get("prompt_tokens", 0) + task_browser_agg.get("prompt_tokens", 0))
        task.completion_tokens = int(
            task_completion_agg.get("completion_tokens", 0) + task_browser_agg.get("completion_tokens", 0)
        )
        task.total_tokens = int(task_completion_agg.get("total_tokens", 0) + task_browser_agg.get("total_tokens", 0))
        task.cached_tokens = int(task_completion_agg.get("cached_tokens", 0) + task_browser_agg.get("cached_tokens", 0))

        task.input_cost_total = task_completion_agg.get("input_cost_total", Decimal("0")) + task_browser_agg.get(
            "input_cost_total", Decimal("0")
        )
        task.input_cost_uncached = task_completion_agg.get("input_cost_uncached", Decimal("0")) + task_browser_agg.get(
            "input_cost_uncached", Decimal("0")
        )
        task.input_cost_cached = task_completion_agg.get("input_cost_cached", Decimal("0")) + task_browser_agg.get(
            "input_cost_cached", Decimal("0")
        )
        task.output_cost = task_completion_agg.get("output_cost", Decimal("0")) + task_browser_agg.get(
            "output_cost", Decimal("0")
        )
        task.total_cost = task_completion_agg.get("total_cost", Decimal("0")) + task_browser_agg.get(
            "total_cost", Decimal("0")
        )

        task.credits_cost = task_step_agg.get("credits_cost", Decimal("0")) + task_browser_agg.get(
            "credits_cost", Decimal("0")
        )

        task.updated_at = timezone.now()
        task.save(update_fields=task_update_fields)

def _update_suite_state(suite_run_id) -> None:
    if not suite_run_id:
        return
    try:
        suite = (
            EvalSuiteRun.objects.select_related("shared_agent")
            .prefetch_related("runs")
            .get(id=suite_run_id)
        )
    except EvalSuiteRun.DoesNotExist:
        return

    runs = list(suite.runs.all())
    started_at_values = [r.started_at for r in runs if r.started_at]
    finished_at_values = [r.finished_at for r in runs if r.finished_at]

    if not runs:
        status = EvalSuiteRun.Status.ERRORED
        started_at = suite.started_at or timezone.now()
        finished_at = timezone.now()
    else:
        if any(r.status == EvalRun.Status.ERRORED for r in runs):
            status = EvalSuiteRun.Status.ERRORED
        elif any(r.status in (EvalRun.Status.PENDING, EvalRun.Status.RUNNING) for r in runs):
            status = EvalSuiteRun.Status.RUNNING
        else:
            status = EvalSuiteRun.Status.COMPLETED

        started_at = min(started_at_values) if started_at_values else suite.started_at
        finished_at = max(finished_at_values) if finished_at_values else suite.finished_at

    suite.status = status
    suite.started_at = started_at
    suite.finished_at = finished_at
    suite.save(update_fields=["status", "started_at", "finished_at", "updated_at"])
    broadcast_suite_update(suite, include_runs=False)


def _finalize_pending_tasks(run: EvalRun, final_status: str) -> None:
    """
    Mark any leftover pending/running tasks as skipped (if completed) or failed (if errored).
    This keeps UI in sync when a scenario exits early.
    """
    terminal_status = (
        EvalRunTask.Status.SKIPPED
        if final_status == EvalRun.Status.COMPLETED
        else EvalRunTask.Status.FAILED
    )
    now = timezone.now()
    pending_statuses = (
        EvalRunTask.Status.PENDING,
        EvalRunTask.Status.RUNNING,
    )
    for task in run.tasks.filter(status__in=pending_statuses):
        task.status = terminal_status
        if task.started_at is None:
            task.started_at = now
        task.finished_at = now
        if not task.observed_summary:
            task.observed_summary = "Automatically marked when run finished."
        task.save(update_fields=["status", "started_at", "finished_at", "observed_summary", "updated_at"])
        try:
            broadcast_task_update(task)
        except Exception:
            logger.debug("Broadcast task update failed during finalize", exc_info=True)

class EvalRunner:
    """
    Executes a single evaluation run.
    """
    def __init__(self, run_id: str):
        self.run = EvalRun.objects.get(id=run_id)
        self.scenario = ScenarioRegistry.get(self.run.scenario_slug)
        if not self.scenario:
            raise ValueError(f"Scenario {self.run.scenario_slug} not found in registry.")

    def execute(self) -> None:
        """
        Main execution method.
        """
        logger.info(f"Starting eval run {self.run.id} for scenario {self.run.scenario_slug}")
        
        self.run.status = EvalRun.Status.RUNNING
        self.run.started_at = timezone.now()
        self.run.save(update_fields=['status', 'started_at'])
        broadcast_run_update(self.run)
        _update_suite_state(self.run.suite_run_id)

        try:
            # Pre-create tasks for visibility
            # We wipe existing tasks if this is a re-run to avoid duplicates
            self.run.tasks.all().delete()
            
            for i, task_def in enumerate(self.scenario.tasks, start=1):
                EvalRunTask.objects.create(
                    run=self.run,
                    sequence=i,
                    name=task_def.name,
                    assertion_type=task_def.assertion_type,
                    status=EvalRunTask.Status.PENDING
                )

            # Run the scenario logic
            self.scenario.run(str(self.run.id), str(self.run.agent_id))

            # Mark completion
            self.run.status = EvalRun.Status.COMPLETED

        except Exception as e:
            logger.exception(f"Eval run {self.run.id} failed with exception")
            self.run.status = EvalRun.Status.ERRORED
            self.run.notes = f"Exception:\n{str(e)}\n\nTraceback:\n{traceback.format_exc()}"
        
        finally:
            _finalize_pending_tasks(self.run, self.run.status)
            self.run.finished_at = timezone.now()
            self.run.save()
            try:
                _aggregate_eval_run_metrics(self.run)
                self.run.refresh_from_db()
            except Exception:
                logger.exception("Failed to aggregate eval run metrics for %s", self.run.id)
            logger.info(f"Finished eval run {self.run.id} with status {self.run.status}")
            broadcast_run_update(self.run, include_tasks=True)
            _update_suite_state(self.run.suite_run_id)
