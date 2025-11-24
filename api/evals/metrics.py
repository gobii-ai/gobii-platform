import logging
from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from api.models import (
    BrowserUseAgentTask,
    EvalRun,
    EvalRunTask,
    PersistentAgentCompletion,
    PersistentAgentStep,
)

logger = logging.getLogger(__name__)

def _decimal_zero(output_digits: int = 20, output_places: int = 6) -> Value:
    return Value(Decimal("0"), output_field=DecimalField(max_digits=output_digits, decimal_places=output_places))

def aggregate_task_metrics(task: EvalRunTask, *, window_start, window_end) -> None:
    """Aggregates usage/cost metrics for a single EvalRunTask within a provided window."""
    if window_start is None:
        return

    dec_zero = _decimal_zero()
    int_zero = Value(0)

    window = {
        "eval_run_id": task.run_id,
        "created_at__gte": window_start,
        "created_at__lte": window_end,
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
    task.save(update_fields=[
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
    ])


def aggregate_run_metrics(run: EvalRun) -> None:
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

    # Aggregate per-task windows using contiguous boundaries between task start times
    ordered_tasks = list(run.tasks.all())
    if not ordered_tasks:
        return

    # Build sorted tasks to derive boundaries
    ordered_tasks.sort(key=lambda t: (t.sequence, t.started_at or timezone.datetime.min.replace(tzinfo=timezone.utc)))

    now = timezone.now()
    for idx, task in enumerate(ordered_tasks):
        if not task.started_at:
            continue
        window_start = task.started_at
        if idx + 1 < len(ordered_tasks):
            window_end = ordered_tasks[idx + 1].started_at or run.finished_at or now
        else:
            window_end = run.finished_at or now
        aggregate_task_metrics(task, window_start=window_start, window_end=window_end)
