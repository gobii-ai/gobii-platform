
import logging
from celery import shared_task
from api.evals.runner import EvalRunner
from api.evals.gc import garbage_collect_eval_runs

logger = logging.getLogger(__name__)

@shared_task(bind=True, name="api.evals.tasks.run_eval_task")
def run_eval_task(self, run_id: str):
    """
    Celery task to execute an EvalRun.
    """
    logger.info(f"Celery task received for EvalRun {run_id}")
    try:
        runner = EvalRunner(run_id)
        runner.execute()
    except Exception as e:
        logger.exception(f"Failed to initialize or execute EvalRunner for {run_id}")
        raise e


@shared_task(bind=True, name="api.evals.tasks.gc_eval_runs")
def gc_eval_runs_task(self, max_age_minutes: int = 30):
    """
    Mark stale eval runs/suites as errored.
    """
    return garbage_collect_eval_runs(max_age_minutes=max_age_minutes)
