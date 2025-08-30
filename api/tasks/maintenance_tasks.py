import os
import logging
import tempfile
import time

from celery import shared_task
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta

from observability import traced

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def cleanup_temp_files(self) -> None:
    """
    Clean up temporary files.
    
    This task runs periodically to clean up any temporary files that may
    have been left behind by various processes.
    """
    with traced("MAINTENANCE Cleanup Temp Files") as span:
        try:
            temp_dir = tempfile.gettempdir()
            logger.info("Starting cleanup of temporary files in %s", temp_dir)

            # Clean up files older than 24 hours
            cutoff_time = time.time() - (24 * 60 * 60)
            cleaned_count = 0

            span.set_attribute('cutoff_time', cutoff_time)

            for filename in os.listdir(temp_dir):
                file_path = os.path.join(temp_dir, filename)
                try:
                    if os.path.isfile(file_path) and os.path.getmtime(file_path) < cutoff_time:
                        # Only clean up files that look like they might be from our app
                        if any(pattern in filename.lower() for pattern in ['gobii', 'tmp', 'temp']):
                            os.remove(file_path)
                            cleaned_count += 1
                except (OSError, PermissionError):
                    # Skip files we can't access
                    continue

            span.set_attribute('cleaned_count', cleaned_count)

            logger.info("Cleanup completed: removed %d temporary files", cleaned_count)

        except Exception as e:
            logger.exception("Error during temporary file cleanup: %s", str(e))

@shared_task(bind=True, ignore_result=True, acks_late=True)
def garbage_collect_timed_out_tasks(self) -> None:
    """
    Garbage collect browser agent tasks that have truly timed out.
    
    Marks tasks as FAILED if they've been running longer than the Celery timeout
    (4 hours) and are still in PENDING or IN_PROGRESS status.
    """
    with traced("MAINTENANCE Garbage Collect Timed Out Tasks") as span:
        try:
            from ..models import BrowserUseAgentTask
            
            # Calculate cutoff time (4 hours ago)
            timeout_hours = 4
            cutoff_time = timezone.now() - timedelta(hours=timeout_hours)
            
            logger.info("Starting garbage collection of timed-out tasks created before %s", cutoff_time)
            
            # Find tasks that are still running but should have timed out
            timed_out_tasks = BrowserUseAgentTask.objects.filter(
                created_at__lt=cutoff_time,
                status__in=[
                    BrowserUseAgentTask.StatusChoices.PENDING,
                    BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
                ],
                is_deleted=False,
            )
            
            task_count = timed_out_tasks.count()
            span.set_attribute('timeout_hours', timeout_hours)
            span.set_attribute('cutoff_time', cutoff_time.isoformat())
            span.set_attribute('found_timed_out_tasks', task_count)
            
            if task_count == 0:
                logger.info("No timed-out tasks found")
                span.add_event("No timed-out tasks found")
                return
            
            # Update tasks to FAILED status
            updated_count = timed_out_tasks.update(
                status=BrowserUseAgentTask.StatusChoices.FAILED,
                error_message=f"Task timed out after {timeout_hours} hours",
                updated_at=timezone.now()
            )
            
            span.set_attribute('updated_count', updated_count)
            span.add_event(f"Marked {updated_count} tasks as timed out")
            
            logger.info(
                "Garbage collection completed: marked %d tasks as timed out (created before %s)",
                updated_count,
                cutoff_time
            )
            
        except Exception as e:
            logger.exception("Error during timed-out task garbage collection: %s", str(e))
            span.set_attribute('error', str(e))
            raise  # Re-raise to trigger Celery retry if configured


@shared_task(name="prune_usage_threshold_sent")
def prune_usage_threshold_sent():
    call_command("prune_usage_threshold_sent")