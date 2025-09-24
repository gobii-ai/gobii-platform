import sys
import warnings

import logging
logging.getLogger("opentelemetry").setLevel(logging.DEBUG)
from django.apps import AppConfig
from observability import init_tracing, GobiiService   # adjust import path if observability lives elsewhere

from config.stripe_config import refresh_django_stripe_secret_settings


logger = logging.getLogger(__name__)

class TracingInitialization(AppConfig):
    name = "config"          # the dotted-path of the package
    verbose_name = "Tracing Initialization"

    def ready(self):
        logger.info("Starting OpenTelemetry initialization...")

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Accessing the database during app initialization is discouraged.",
                category=RuntimeWarning,
            )
            refresh_django_stripe_secret_settings(force_reload=True)

        if any(arg.find("celery") != -1 for arg in sys.argv):
            logger.info("Skipping OpenTelemetry initialization for Celery worker; will be initialized in worker_process_init_handler")
            return
        else:
            service = GobiiService.WEB

        logger.info(f"Initializing OpenTelemetry for service: {service.value}")
        init_tracing(service)
        logger.info("OpenTelemetry initialized successfully")
