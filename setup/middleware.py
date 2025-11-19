from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

try:
    from api.agent.core.llm_config import is_llm_bootstrap_required
except Exception:  # pragma: no cover - avoid import errors during migrations
    def is_llm_bootstrap_required(*_, **__):  # type: ignore[override]
        return True


_ALLOWED_PREFIXES = (
    "/setup/",
    "/static/",
    "/favicon.ico",
    "/eval/",  # Allow eval paths to bypass first-run setup
)
_ALLOWED_EXACT = {
    "/healthz/",
}


def is_initial_setup_complete(*, force_refresh: bool = False) -> bool:
    User = get_user_model()
    try:
        has_superuser = User.objects.filter(is_superuser=True).exists()
    except Exception:
        return False
    if not has_superuser:
        return False
    try:
        needs_llm = is_llm_bootstrap_required(force_refresh=force_refresh)
    except Exception:
        return False
    return not needs_llm


class FirstRunSetupMiddleware:
    """Redirect every request to the setup wizard until configuration is complete."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not getattr(settings, "FIRST_RUN_SETUP_ENABLED", True):
            request.is_first_run = False
            return self.get_response(request)

        request.is_first_run = not is_initial_setup_complete()

        if request.is_first_run and self._should_redirect(request):
            return redirect(reverse("setup:wizard"))

        response = self.get_response(request)
        return response

    def _should_redirect(self, request: HttpRequest) -> bool:
        path = request.path

        if any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
            return False
        if path in _ALLOWED_EXACT:
            return False
        if request.method == "POST" and path.startswith("/setup/"):
            return False

        try:
            wizard_url = reverse("setup:wizard")
        except Exception:  # URL conf not ready yet
            return False
        if path == wizard_url:
            return False

        return True
