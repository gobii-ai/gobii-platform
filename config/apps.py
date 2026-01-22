import sys

import logging
logging.getLogger("opentelemetry").setLevel(logging.DEBUG)
from django.apps import AppConfig
from observability import init_tracing, init_profiling, GobiiService   # adjust import path if observability lives elsewhere


logger = logging.getLogger(__name__)

class TracingInitialization(AppConfig):
    name = "config"          # the dotted-path of the package
    verbose_name = "Tracing Initialization"

    def ready(self):
        logger.info("Starting OpenTelemetry initialization...")

        if any(arg.find("celery") != -1 for arg in sys.argv):
            logger.info("Skipping OpenTelemetry initialization for Celery worker; will be initialized in worker_process_init_handler")
            return
        else:
            service = GobiiService.WEB

        logger.info(f"Initializing OpenTelemetry for service: {service.value}")
        init_tracing(service)
        logger.info("OpenTelemetry initialized successfully")

        logger.info("Initializing Pyroscope profiling...")
        init_profiling(service)
        logger.info("Pyroscope profiling initialization complete")