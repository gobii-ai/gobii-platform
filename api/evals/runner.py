import logging
import traceback

from django.utils import timezone

from api.models import (
    EvalRun,
    EvalRunTask,
    EvalSuiteRun,
)
from api.evals.registry import ScenarioRegistry
from api.evals.realtime import broadcast_run_update, broadcast_suite_update, broadcast_task_update
from api.evals.metrics import aggregate_run_metrics
from api.evals.execution import set_current_eval_run_id, set_current_eval_routing_profile
from api.evals.fingerprint import compute_scenario_fingerprint, get_code_version, get_code_branch, get_primary_model
import api.evals.loader # noqa: F401

logger = logging.getLogger(__name__)


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

        # Resolve the routing profile from the suite run (if set)
        routing_profile = None
        if self.run.suite_run_id:
            try:
                suite = EvalSuiteRun.objects.select_related("llm_routing_profile").get(
                    id=self.run.suite_run_id
                )
                routing_profile = suite.llm_routing_profile
            except EvalSuiteRun.DoesNotExist:
                pass

        # Snapshot the routing profile on the run for history
        if routing_profile:
            self.run.llm_routing_profile = routing_profile
            self.run.llm_routing_profile_name = routing_profile.name
            self.run.primary_model = get_primary_model(routing_profile)

        # Capture fingerprint and code version for comparison tracking
        self.run.scenario_fingerprint = compute_scenario_fingerprint(self.scenario)
        self.run.code_version = get_code_version()
        self.run.code_branch = get_code_branch()

        self.run.status = EvalRun.Status.RUNNING
        self.run.started_at = timezone.now()
        save_fields = ['status', 'started_at', 'scenario_fingerprint', 'code_version', 'code_branch']
        if routing_profile:
            save_fields.extend(['llm_routing_profile', 'llm_routing_profile_name', 'primary_model'])
        self.run.save(update_fields=save_fields)
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

            # Run the scenario logic with eval context
            set_current_eval_run_id(str(self.run.id))
            set_current_eval_routing_profile(routing_profile)
            try:
                self.scenario.run(str(self.run.id), str(self.run.agent_id))
            finally:
                set_current_eval_run_id(None)
                set_current_eval_routing_profile(None)

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
                aggregate_run_metrics(self.run)
                self.run.refresh_from_db()
            except Exception:
                logger.exception("Failed to aggregate eval run metrics for %s", self.run.id)
            logger.info(f"Finished eval run {self.run.id} with status {self.run.status}")
            broadcast_run_update(self.run, include_tasks=True)
            _update_suite_state(self.run.suite_run_id)
