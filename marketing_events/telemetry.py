import logging
from contextlib import contextmanager

from opentelemetry import trace

_tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)


@contextmanager
def trace_event(evt: dict):
    with _tracer.start_as_current_span("marketing_event") as span:
        span.set_attribute("event.id", evt.get("event_id"))
        span.set_attribute("event.name", evt.get("event_name"))
        span.set_attribute("event.time", evt.get("event_time"))
        yield


def record_fbc_synthesized(*, source: str) -> None:
    """Emit a monitorable log and span event when _fbc is synthesized from fbclid."""
    span = trace.get_current_span()
    if span and span.is_recording():
        span.add_event(
            "marketing.attribution.fbc_synthesized",
            {"source": source},
        )
    logger.info("marketing.attribution.fbc_synthesized source=%s", source)
