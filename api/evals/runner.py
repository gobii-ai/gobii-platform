import logging
import traceback
from django.utils import timezone

from api.models import EvalRun, EvalRunTask, EvalSuiteRun, PersistentAgent
from api.evals.registry import ScenarioRegistry
from api.evals.realtime import broadcast_run_update, broadcast_suite_update
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

    if any(r.status == EvalRun.Status.ERRORED for r in runs):
        status = EvalSuiteRun.Status.ERRORED
    elif any(r.status in (EvalRun.Status.PENDING, EvalRun.Status.RUNNING) for r in runs):
        status = EvalSuiteRun.Status.RUNNING
    elif runs:
        status = EvalSuiteRun.Status.COMPLETED
    else:
        status = EvalSuiteRun.Status.PENDING

    suite.status = status
    suite.started_at = min(started_at_values) if started_at_values else suite.started_at
    suite.finished_at = max(finished_at_values) if finished_at_values else suite.finished_at
    suite.save(update_fields=["status", "started_at", "finished_at", "updated_at"])
    broadcast_suite_update(suite, include_runs=False)

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
            self.run.finished_at = timezone.now()
            self.run.save()
            logger.info(f"Finished eval run {self.run.id} with status {self.run.status}")
            broadcast_run_update(self.run, include_tasks=True)
            _update_suite_state(self.run.suite_run_id)
