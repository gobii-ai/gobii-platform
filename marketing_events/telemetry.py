from contextlib import contextmanager

from opentelemetry import trace

_tracer = trace.get_tracer(__name__)


@contextmanager
def trace_event(evt: dict):
    with _tracer.start_as_current_span("marketing_event") as span:
        span.set_attribute("event.id", evt.get("event_id"))
        span.set_attribute("event.name", evt.get("event_name"))
        span.set_attribute("event.time", evt.get("event_time"))
        yield
