import logging
from datetime import timedelta

from django.contrib.auth import login
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, inline_serializer

from api.models import ApiKey
from api.services.browser_session_tickets import (
    BrowserSessionTicketError,
    BrowserSessionTicketForbidden,
    BrowserSessionTicketInvalid,
    BrowserSessionTicketRateLimited,
    BrowserSessionTicketUnavailable,
    browser_session_ttl_seconds,
    consume_browser_session_ticket,
    issue_browser_session_ticket,
)


logger = logging.getLogger(__name__)


def _protect_ticket_response(response):
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _ticket_landing_response(request):
    csrf_token = get_token(request)
    response = HttpResponse(
        f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Signing in to Gobii</title></head>
<body>
<p>Completing secure sign-in…</p>
<form id="browser-session-form" method="post">
<input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
<input id="browser-session-token" type="hidden" name="token">
</form>
<script>
window.addEventListener("DOMContentLoaded", () => {{
  const token = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
  history.replaceState(null, "", window.location.pathname);
  document.getElementById("browser-session-token").value = token;
  setTimeout(() => document.getElementById("browser-session-form").submit(), 0);
}}, {{ once: true }});
</script>
</body>
</html>""",
        content_type="text/html; charset=utf-8",
    )
    response["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return _protect_ticket_response(response)


@extend_schema(
    operation_id="createBrowserSessionTicket",
    tags=["utils"],
    request=inline_serializer(
        name="BrowserSessionTicketRequest",
        fields={
            "expected_environment": serializers.CharField(),
            "next_path": serializers.CharField(required=False),
            "purpose": serializers.CharField(required=False),
        },
    ),
    responses={
        201: inline_serializer(
            name="BrowserSessionTicketResponse",
            fields={
                "login_url": serializers.URLField(),
                "expires_at": serializers.DateTimeField(),
                "environment": serializers.CharField(),
            },
        )
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_browser_session_ticket(request):
    api_key = request.auth if isinstance(request.auth, ApiKey) else None
    if api_key is None:
        return _protect_ticket_response(
            Response(
                {"detail": "A personal API key is required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        )

    try:
        issued = issue_browser_session_ticket(
            user=request.user,
            api_key=api_key,
            expected_environment=request.data.get("expected_environment"),
            request_host=request.get_host(),
            next_path=request.data.get("next_path"),
            purpose=request.data.get("purpose", ""),
        )
    except BrowserSessionTicketForbidden as exc:
        return _protect_ticket_response(
            Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        )
    except BrowserSessionTicketUnavailable as exc:
        return _protect_ticket_response(
            Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        )
    except BrowserSessionTicketInvalid as exc:
        return _protect_ticket_response(
            Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        )
    except BrowserSessionTicketRateLimited as exc:
        response = Response(
            {"detail": str(exc)},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        response["Retry-After"] = "60"
        return _protect_ticket_response(response)

    logger.info(
        "Issued browser session ticket %s for user %s in %s via API key %s",
        issued.ticket.id,
        issued.ticket.user_id,
        issued.ticket.environment,
        issued.ticket.api_key_id,
    )
    return _protect_ticket_response(
        Response(
            {
                "login_url": issued.login_url,
                "expires_at": issued.ticket.expires_at,
                "environment": issued.ticket.environment,
            },
            status=status.HTTP_201_CREATED,
        )
    )


# The single-use fragment token authenticates this non-production login. Some
# headless browsers serialize the form Origin as "null", so Django's
# origin-based CSRF check would reject the intended browser while adding no
# protection against a caller that already possesses the bearer token.
@csrf_exempt
@require_http_methods(["GET", "POST"])
def consume_browser_session_ticket_view(request, ticket_id):
    if request.method == "GET":
        return _ticket_landing_response(request)

    try:
        ticket = consume_browser_session_ticket(
            ticket_id=ticket_id,
            raw_token=request.POST.get("token", ""),
            request_host=request.get_host(),
        )
        session_ttl_seconds = browser_session_ttl_seconds()
    except BrowserSessionTicketError:
        return _protect_ticket_response(
            HttpResponse(
                "This browser session ticket is invalid, expired, or already used.",
                status=410,
                content_type="text/plain; charset=utf-8",
            )
        )

    login(
        request,
        ticket.user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
    request.session.set_expiry(
        timezone.now() + timedelta(seconds=session_ttl_seconds)
    )
    logger.info(
        "Consumed browser session ticket %s for user %s in %s",
        ticket.id,
        ticket.user_id,
        ticket.environment,
    )
    return _protect_ticket_response(redirect(ticket.next_path))
