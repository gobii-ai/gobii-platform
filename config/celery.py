import pathlib, sys, os
from pathlib import Path
from celery import Celery
from celery.signals import worker_ready, worker_shutdown, worker_process_init, task_prerun, task_postrun
from .bootsteps import LivenessProbe

# Ensure the browser-use task counter signal handlers are registered
import celery_task_counter  # noqa: F401

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# File for validating worker readiness
READINESS_FILE = Path('/tmp/celery_ready')


app = Celery('gobii_platform')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Add liveness probe to celery app
app.steps['worker'].add(LivenessProbe)

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()


@worker_ready.connect
def worker_ready_handler(**_):
    print(f"Worker ready signal received, creating readiness file: {READINESS_FILE}")
    READINESS_FILE.touch()
    print(f"Readiness file created successfully")

@worker_process_init.connect
def worker_process_init_handler(**_):
    """
    Initialize OpenTelemetry for each worker process after forking.
    This ensures each worker child has its own BatchSpanProcessor thread.
    """
    print(f"Worker process init signal received, initializing OpenTelemetry for PID {os.getpid()}")
    
    from observability import init_tracing, GobiiService
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor

    init_tracing(GobiiService.WORKER)
    CeleryInstrumentor().instrument()
    LoggingInstrumentor().instrument(set_logging_format=True)
    
    print(f"OpenTelemetry initialization completed for worker PID {os.getpid()}")

@task_prerun.connect
def task_prerun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, **kwds):
    """
    Close old database connections before each task runs.
    This ensures we start with fresh connections and prevents connection timeout issues.
    """
    from django.db import close_old_connections
    close_old_connections()

@task_postrun.connect  
def task_postrun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, retval=None, state=None, **kwds):
    """
    Close old database connections after each task completes.
    This prevents connection leaks and ensures clean cleanup.
    """
    from django.db import close_old_connections
    close_old_connections()

@worker_shutdown.connect
def worker_shutdown_handler(**_):
    print(f"Worker shutdown signal received, removing readiness file: {READINESS_FILE}")
    READINESS_FILE.unlink(missing_ok=True)
    print(f"Readiness file removed")
    
    # Cleanup task counter database for fresh start in local development
    print(f"Cleaning up task counter database...")
    try:
        from celery_task_counter import cleanup_task_counter_db
        cleanup_task_counter_db()
        print(f"Task counter database cleanup completed")
    except Exception as e:
        print(f"Error during task counter database cleanup: {e}")
    
    # Shutdown OpenTelemetry to prevent hanging during worker termination
    print(f"Shutting down OpenTelemetry tracing...")
    try:
        from observability import shutdown_tracing
        shutdown_tracing()
        print(f"OpenTelemetry shutdown completed")
    except Exception as e:
        print(f"Error during OpenTelemetry shutdown: {e}")

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
