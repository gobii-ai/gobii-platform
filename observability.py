# platform/observability.py
import os
from enum import Enum
from functools import lru_cache
import logging
import signal
import threading

from config import settings

logger = logging.getLogger(__name__)

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor, Span, Status, StatusCode
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from typing import Optional
from opentelemetry import trace, baggage, context
from contextlib import contextmanager

tracer = trace.get_tracer("gobii.utils")

# Global reference to the tracer provider for cleanup
_tracer_provider: Optional[TracerProvider] = None

# Flag indicating whether tracing was actually initialised (provider installed).
# Local tests disable tracing; in that case we suppress noisy INFO logs.
_TRACING_ACTIVE: bool = False

# Global reference for Pyroscope profiling cleanup
_pyroscope_initialized: bool = False

class DaemonOTLPSpanExporter(OTLPSpanExporter):
    """
    Custom OTLPSpanExporter that ensures the BatchSpanProcessor's worker thread
    is set as daemon to prevent hanging during Celery worker shutdown.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    def shutdown(self):
        """Override shutdown to ensure clean exit even if thread is stuck."""
        try:
            # Try the normal shutdown first
            super().shutdown()
        except Exception as e:
            logger.warning(f"OTLPSpanExporter shutdown failed: {e}")


class DaemonBatchSpanProcessor(BatchSpanProcessor):
    """
    Custom BatchSpanProcessor that sets its worker thread as daemon
    to prevent blocking sys.exit() during Celery worker shutdown.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the worker thread as daemon after initialization
        # The worker thread is stored in the _worker_thread attribute
        if hasattr(self, '_worker_thread') and self._worker_thread:
            self._worker_thread.daemon = True
            logger.debug("BatchSpanProcessor worker thread set as daemon")
        elif hasattr(self, 'worker_thread') and self.worker_thread:
            self.worker_thread.daemon = True
            logger.debug("BatchSpanProcessor worker thread set as daemon")
        else:
            logger.warning("Could not find BatchSpanProcessor worker thread to set as daemon")

class GobiiService(str, Enum):
    """
    Enum representing the different Gobii services.
    Used for identifying service names in tracing and observability.
    """
    WEB = "gobii-web"
    WORKER = "gobii-worker"

@lru_cache(maxsize=1)                    # make sure we initialize only once
def init_tracing(service_name: GobiiService) -> None:
    """
    Initialize the OTEL tracer provider exactly once per process.
    Pass a `service_name` that distinguishes web vs. workers.
    
    Recommended environment variables for Celery workers:
    - OTEL_SPAN_PROCESSOR=simple                    # Use SimpleSpanProcessor (no threads)
    - OTEL_BSP_SCHEDULE_DELAY=500                   # Fast export (if using batch)
    - OTEL_BSP_EXPORT_TIMEOUT=5000                  # Short timeout (if using batch)
    - OTEL_BSP_MAX_QUEUE_SIZE=1024                  # Small queue (if using batch)
    - OTEL_BSP_MAX_EXPORT_BATCH_SIZE=256            # Smaller than default 512
    """
    global _tracer_provider
    
    logger.debug(f"OpenTelemetry: Initializing OTEL tracer for {service_name.value}")

    # ────────── Decide whether to enable tracing ──────────
    release_env = os.getenv("GOBII_RELEASE_ENV", "local").lower()

    truthy  = ("1", "true", "yes", "on")
    falsy   = ("0", "false", "no", "off")

    user_flag = os.getenv("GOBII_ENABLE_TRACING", "").lower()

    # 1.  If the developer explicitly set a falsy flag, always disable.
    if user_flag in falsy:
        logger.debug("OpenTelemetry: Tracing explicitly disabled via GOBII_ENABLE_TRACING env var – skipping initialization")
        return

    # 2.  If we are in a *local* environment (default during dev/tests) or a
    #     build context (Docker build stage) and the user did *not* explicitly
    #     opt-in with a truthy flag, disable tracing.
    if release_env in {"local", "build"} and user_flag not in truthy:
        logger.debug(
            "OpenTelemetry: %s environment detected with no explicit opt-in – tracing disabled",
            release_env,
        )
        return

    # Otherwise: proceed with normal initialization (current behaviour for
    # preview/staging/prod, or when the developer opted-in locally).

    res = Resource.create(
        {
            "service.name": service_name.value,
            "service.version": os.getenv("GOBII_VERSION", "dev"),
            "deployment.environment.name": os.getenv("GOBII_RELEASE_ENV", "local"),
        }
    )

    logger.debug(f"OpenTelemetry: OTEL resource: {res}")

    # Create a TracerProvider with the resource and add processors
    try:
        provider = TracerProvider(resource=res)
        logger.debug(f"OpenTelemetry: TracerProvider created with resource: {res}")
        
        # Choose span processor type based on environment variable
        # SimpleSpanProcessor = synchronous, no threads -- can be useful for debugging
        # BatchSpanProcessor = asynchronous, with daemon threads, better performance -- generally recommended
        processor_type = os.getenv("OTEL_SPAN_PROCESSOR", "batch").lower()
        
        if processor_type == "simple":
            # Use SimpleSpanProcessor for completely synchronous operation
            # No background threads = no hanging issues during shutdown
            span_processor = SimpleSpanProcessor(DaemonOTLPSpanExporter(
                endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
                insecure=settings.OTEL_EXPORTER_OTLP_INSECURE,
            ))
            logger.debug("OpenTelemetry: Using SimpleSpanProcessor (synchronous, no background threads)")
            
        else:
            # Use BatchSpanProcessor with aggressive timeouts and daemon threads
            schedule_delay_ms = int(os.getenv("OTEL_BSP_SCHEDULE_DELAY", "500"))  # Much shorter than default 5000ms
            export_timeout_ms = int(os.getenv("OTEL_BSP_EXPORT_TIMEOUT", "5000"))  # Much shorter than default 30000ms
            max_queue_size = int(os.getenv("OTEL_BSP_MAX_QUEUE_SIZE", "1024"))  # Smaller than default 2048
            max_batch_size = int(os.getenv("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "256"))  # Smaller than default 512
            
            span_processor = DaemonBatchSpanProcessor(
                DaemonOTLPSpanExporter(
                    endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
                    insecure=settings.OTEL_EXPORTER_OTLP_INSECURE,
                ),
                schedule_delay_millis=schedule_delay_ms,
                export_timeout_millis=export_timeout_ms,
                max_export_batch_size=max_batch_size,
                max_queue_size=max_queue_size,
            )
            logger.debug(f"OpenTelemetry: Using DaemonBatchSpanProcessor with schedule_delay={schedule_delay_ms}ms, "
                       f"export_timeout={export_timeout_ms}ms, max_queue_size={max_queue_size}, "
                       f"max_batch_size={max_batch_size}")
        
        provider.add_span_processor(span_processor)
        logger.debug(f"OpenTelemetry: Span processor ({processor_type}) and DaemonOTLPSpanExporter added to TracerProvider")

        logger_provider = LoggerProvider(resource=res)
        set_logger_provider(logger_provider)
        logger.debug("OpenTelemetry: LoggerProvider set with resource")

        exporter = OTLPLogExporter(
            endpoint=settings.OTEL_EXPORTER_OTLP_LOG_ENDPOINT,  # Adjust endpoint as needed
        )

        logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))

        # Create and attach an OpenTelemetry handler to the Python root logger
        handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        logging.getLogger().addHandler(handler)

        provider.add_span_processor(TaskIdAttributeProcessor())
        logger.debug("OpenTelemetry: TaskIdAttributeProcessor added to TracerProvider")
        provider.add_span_processor(UserIdAttributeProcessor())
        logger.debug("OpenTelemetry: UserIdAttributeProcessor added to TracerProvider")
        trace.set_tracer_provider(provider)
        logger.debug("OpenTelemetry: provider set as the global tracer provider")
        
        # Store reference for cleanup
        _tracer_provider = provider

        # Mark tracing as active so helper functions can adjust logging verbosity
        global _TRACING_ACTIVE
        _TRACING_ACTIVE = True

    except Exception as e:
        logger.error(f"Failed to initialize OTEL tracer: {e}")
        raise

def shutdown_tracing() -> None:
    """
    Shutdown the tracer provider with timeout to prevent hanging.
    This should be called during worker shutdown.
    
    Uses a separate thread with a timeout to prevent indefinite blocking
    during Celery worker shutdown.
    """
    global _tracer_provider
    
    if _tracer_provider is None:
        logger.debug("OpenTelemetry: No tracer provider to shutdown")
        return
        
    logger.debug("OpenTelemetry: Starting tracer provider shutdown...")
    
    # Create a thread to handle the shutdown with timeout
    shutdown_complete = threading.Event()
    shutdown_exception = None
    
    def shutdown_worker():
        nonlocal shutdown_exception
        try:
            # Force flush first to try to export any pending spans quickly
            logger.debug("OpenTelemetry: Force flushing pending spans...")
            _tracer_provider.force_flush(timeout_millis=3000)  # 3 second flush timeout
            
            # Then shutdown
            logger.debug("OpenTelemetry: Shutting down tracer provider...")
            _tracer_provider.shutdown()
            logger.debug("OpenTelemetry: Tracer provider shutdown completed successfully")
        except Exception as e:
            shutdown_exception = e
            logger.error(f"OpenTelemetry: Error during tracer provider shutdown: {e}")
        finally:
            shutdown_complete.set()
    
    # Start shutdown in separate thread
    shutdown_thread = threading.Thread(target=shutdown_worker, daemon=True)
    shutdown_thread.start()
    
    # Wait for completion with timeout
    timeout_seconds = 10  # 10 second timeout for shutdown
    if shutdown_complete.wait(timeout=timeout_seconds):
        if shutdown_exception:
            logger.error(f"OpenTelemetry: Shutdown completed with error: {shutdown_exception}")
        else:
            logger.debug("OpenTelemetry: Shutdown completed successfully")
    else:
        logger.warning(f"OpenTelemetry: Shutdown timed out after {timeout_seconds} seconds")
    
    # Clean up reference regardless of success/failure
    _tracer_provider = None


def init_profiling(service_name: GobiiService) -> None:
    """
    Initialize Pyroscope profiling exactly once per process.
    Follows same environment-based enable/disable logic as tracing.
    """
    global _pyroscope_initialized

    if _pyroscope_initialized:
        return

    # Check if Pyroscope is configured
    server_address = os.getenv("PYROSCOPE_SERVER_ADDRESS", "")
    if not server_address:
        logger.debug("Pyroscope: No server address configured - skipping")
        return

    # Same environment checks as tracing
    release_env = os.getenv("GOBII_RELEASE_ENV", "local").lower()
    user_flag = os.getenv("GOBII_ENABLE_PROFILING", "").lower()

    truthy = ("1", "true", "yes", "on")
    falsy = ("0", "false", "no", "off")

    if user_flag in falsy:
        logger.debug("Pyroscope: Profiling explicitly disabled")
        return

    if release_env in {"local", "build"} and user_flag not in truthy:
        logger.debug("Pyroscope: %s environment with no opt-in - disabled", release_env)
        return

    try:
        import pyroscope

        auth_token = os.getenv("PYROSCOPE_AUTH_TOKEN", "")
        sample_rate = int(os.getenv("PYROSCOPE_SAMPLE_RATE", "100"))

        pyroscope.configure(
            application_name=service_name.value,
            server_address=server_address,
            auth_token=auth_token if auth_token else None,
            sample_rate=sample_rate,
            detect_subprocesses=True,  # Important for Celery
            oncpu=True,                # CPU profiling
            gil_only=True,             # Focus on GIL-holding code (more relevant for Python)
            enable_logging=True,
            tags={
                "env": os.getenv("GOBII_RELEASE_ENV", "local"),
                "version": os.getenv("GOBII_VERSION", "dev"),
            }
        )

        _pyroscope_initialized = True
        logger.info(f"Pyroscope: Profiling initialized for {service_name.value}")

    except Exception as e:
        logger.error(f"Pyroscope: Failed to initialize - {e}")


def shutdown_profiling() -> None:
    """Shutdown Pyroscope profiling gracefully."""
    global _pyroscope_initialized

    if not _pyroscope_initialized:
        return

    try:
        import pyroscope
        pyroscope.shutdown()
        logger.debug("Pyroscope: Shutdown completed")
    except Exception as e:
        logger.warning(f"Pyroscope: Shutdown error - {e}")
    finally:
        _pyroscope_initialized = False


@contextmanager
def traced(name: str, **attrs):
    # For local/dev runs where tracing is inactive, keep silent (DEBUG at most).
    if _TRACING_ACTIVE:
        logger.debug(f"OpenTelemetry: Tracing {name} with attributes: {attrs}")
    else:
        logger.debug(f"Tracing suppressed (inactive): {name} {attrs}")

    # 1️⃣ create a child of whichever span is current right now (will be NoOp if inactive)
    with tracer.start_as_current_span(name) as span:
        # 2️⃣ attach the initial attributes you supplied
        for key, value in attrs.items():
            span.set_attribute(key, value)
        try:
            # 3️⃣ hand control back to your code while this span is "current"
            yield span            # you can still call span.set_attribute(...) inside
        except Exception as e:
            # Record exception and mark span as ERROR
            mark_span_failed_with_exception(span, e)
            # re-raise to preserve original behavior
            raise
        # span automatically ends when we exit the context


def mark_span_failed(span: Span, *, error_type: Optional[str] = None, message: Optional[str] = None, **attrs) -> None:
    """
    Mark a span as failed without raising an exception.

    This sets the span status to ERROR (so tail-sampling will keep the trace)
    and annotates with helpful attributes for search/grouping.
    """
    try:
        if span.is_recording():
            span.set_status(Status(StatusCode.ERROR, message))
            span.set_attribute("gobii.error", True)
            if error_type:
                span.set_attribute("error.type", error_type)
            if message:
                span.set_attribute("error.message", message)
            for k, v in attrs.items():
                span.set_attribute(k, v)
    except Exception:  # best-effort, never fail caller due to telemetry
        pass

def mark_span_failed_with_exception(span: Span, exc: Exception, message: Optional[str] = None) -> None:
    """
    Mark a span as failed with an exception.

    This sets the span status to ERROR and records the exception.
    """
    try:
        if span.is_recording():
            if message is None:
                message = str(exc)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, message))
            span.set_attribute("gobii.error", True)
            span.set_attribute("error.type", type(exc).__name__)
            span.set_attribute("error.message", message)
    except Exception:  # best-effort, never fail caller due to telemetry
        pass

# Takes a dictionary, and returns a new dictionary with the keys prefixed with the parameter `prefix`, plus a dot.
def dict_to_attributes(d: dict, prefix: str) -> dict:
    """
    Takes a dictionary and returns a new dictionary with the keys prefixed with the parameter `prefix`, plus a dot.
    """
    if not isinstance(d, dict):
        return {}

    return {f"{prefix}.{k}": v for k, v in d.items()}

class TaskIdAttributeProcessor(SpanProcessor):
    def on_start(self, span, parent_context: Optional[context.Context] = None) -> None:
        """
        Add the task ID from the baggage to the span attributes if it exists.
        """
        task_id = baggage.get_baggage("task.id", parent_context)

         # If the task ID exists, set it as an attribute on the span
        if task_id:
            # Set the task ID as an attribute on the span - we know that's a string
            span.set_attribute("task.id", str(task_id))
            logger.debug(f"OpenTelemetry: Task ID set on span: {task_id}")
        else:
            logger.debug("OpenTelemetry: No task ID found in baggage, not setting on span")


class UserIdAttributeProcessor(SpanProcessor):
    def on_start(self, span, parent_context: Optional[context.Context] = None) -> None:
        """
        Add the User ID from the baggage to the span attributes if it exists.
        """
        user_id = baggage.get_baggage("user.id", parent_context)

        if user_id:
            # Set the task ID as an attribute on the span - we know that's a string
            span.set_attribute("user.id", str(user_id))
            logger.debug(f"OpenTelemetry: User ID set on span: {user_id}")
        else:
            logger.debug("OpenTelemetry: No user ID found in baggage, not setting on span")
