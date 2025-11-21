from datetime import timedelta
import logging
from typing import Optional

from django.utils import timezone

from api.models import EvalRun, EvalSuiteRun

logger = logging.getLogger(__name__)


def _run_last_activity(run: EvalRun) -> Optional[timezone.datetime]:
    candidates = [run.updated_at, run.started_at, run.finished_at]
    task_updated = (
        run.tasks.order_by("-updated_at").values_list("updated_at", flat=True).first()
    )
    if task_updated:
        candidates.append(task_updated)
    task_finished = (
        run.tasks.order_by("-finished_at").values_list("finished_at", flat=True).first()
    )
    if task_finished:
        candidates.append(task_finished)
    filtered = [ts for ts in candidates if ts is not None]
    return max(filtered) if filtered else None


def garbage_collect_eval_runs(max_age_minutes: int = 30) -> dict[str, int]:
    """
    Mark stale eval runs/suites as errored.

    Staleness is based on last activity (run/task updates), not just start time,
    so long-running suites can continue as long as they show progress.
    """
    now = timezone.now()
    cutoff = now - timedelta(minutes=max_age_minutes)

    runs_updated = 0
    suites_updated = 0

    candidate_runs = EvalRun.objects.filter(
        status__in=[EvalRun.Status.RUNNING, EvalRun.Status.PENDING],
        finished_at__isnull=True,
    )
    for run in candidate_runs:
        last_activity = _run_last_activity(run) or run.started_at or run.updated_at
        if last_activity and last_activity <= cutoff:
            run.status = EvalRun.Status.ERRORED
            run.finished_at = now
            run.updated_at = now
            run.save(update_fields=["status", "finished_at", "updated_at"])
            runs_updated += 1

    candidate_suites = EvalSuiteRun.objects.filter(
        status=EvalSuiteRun.Status.RUNNING,
        finished_at__isnull=True,
    )
    for suite in candidate_suites:
        run_activities = []
        for child_run in suite.runs.all():
            run_activities.append(_run_last_activity(child_run))
        suite_candidates = [suite.updated_at, suite.started_at] + [ts for ts in run_activities if ts]
        suite_last_activity = max([ts for ts in suite_candidates if ts is not None], default=None)
        if suite_last_activity and suite_last_activity <= cutoff:
            suite.status = EvalSuiteRun.Status.ERRORED
            suite.finished_at = now
            suite.updated_at = now
            suite.save(update_fields=["status", "finished_at", "updated_at"])
            suites_updated += 1

    if runs_updated or suites_updated:
        logger.info(
            "Eval GC marked stale runs/suites as errored (runs=%s suites=%s age>%sm by last activity)",
            runs_updated,
            suites_updated,
            max_age_minutes,
        )

    return {"runs": runs_updated, "suites": suites_updated}
