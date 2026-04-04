from typing import Callable

from django.http import HttpRequest, HttpResponse

from api.services.user_timezone import maybe_infer_user_timezone


TIMEZONE_HEADER = "X-Gobii-Timezone"
TIMEZONE_API_PREFIXES = ("/console/api/", "/api/app/v1/")


class ConsoleApiTimezoneInferenceMiddleware:
    """Persist browser timezone once for authenticated console API requests."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self._maybe_store_inferred_timezone(request)
        return self.get_response(request)

    @staticmethod
    def _maybe_store_inferred_timezone(request: HttpRequest) -> None:
        if not any(request.path.startswith(prefix) for prefix in TIMEZONE_API_PREFIXES):
            return

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return

        timezone_header = request.headers.get(TIMEZONE_HEADER)
        if not timezone_header:
            return

        try:
            maybe_infer_user_timezone(user, timezone_header)
        except ValueError:
            # Ignore invalid browser timezone headers and keep persisted preferences unchanged.
            return
