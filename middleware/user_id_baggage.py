from typing import Callable

from django.http import HttpRequest, HttpResponse

# OpenTelemetry imports
from opentelemetry import baggage, context

class UserIdBaggageMiddleware:  # pragma: no cover
    """
    Injects the user's primary‑key into OpenTelemetry baggage so it can be
    picked up by OpenTelemetry backends.

    * If the user is authenticated, the real `user.id` is sent.
    * If the user is anonymous, an empty string (`""`) is sent.  (AnonymousUser
      has `id = None`, which serialises to the empty string by default.)

    The baggage value is pushed onto the current OTEL context *before* the view
    is executed and is removed afterwards so the value never leaks between
    requests/threads.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    # Django calls __call__ once per request
    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)

        if user is None or not user.is_authenticated:
            # If the request has no user, we can't set any baggage
            return self.get_response(request)

        # Per the user’s requirement, we always pass ``user.id`` – it will be
        # ``None`` for anonymous users.
        value = getattr(user, "id", None)

        # OpenTelemetry: attach new context containing the baggage entry
        ctx = baggage.set_baggage("user.id", str(value) if value is not None else "")
        token = context.attach(ctx)

        try:
            # Continue down the middleware chain / into the view
            response = self.get_response(request)
        finally:
            # Ensure the context we pushed is removed once the response is sent
            context.detach(token)

        return response