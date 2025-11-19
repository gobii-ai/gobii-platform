import logging
import traceback
from django.utils import timezone
from api.models import EvalRun, EvalRunTask, PersistentAgent
from api.evals.registry import ScenarioRegistry
import api.evals.loader # noqa: F401

logger = logging.getLogger(__name__)

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
