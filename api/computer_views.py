from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from api.computer_http import parse_computer_json_payload
from api.models import ComputerPairingSession
from api.services.computer_relay import (
    ComputerRelayError,
    authenticate_relay_access_token,
    computer_client_version_supported,
    computer_rate_limited,
    create_pairing_session,
    pairing_device_code_matches,
    redeem_pairing,
    rotate_refresh_token,
    store_artifact,
)
from util.analytics import Analytics


def _client_ip(request: HttpRequest) -> str:
    return Analytics.get_client_ip(request) or "unknown"


def _relay_url() -> str:
    parsed = urlparse(settings.PUBLIC_SITE_URL)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws/computer/v1/relay/"


@csrf_exempt
@require_POST
def computer_pairing_start(request: HttpRequest):
    if computer_rate_limited(
        f"computer-pairing-start:{_client_ip(request)}",
        limit=settings.COMPUTER_CPP_PAIRING_STARTS_PER_IP_HOUR,
        window_seconds=3600,
    ):
        return JsonResponse({"error": "rate_limited"}, status=429)
    try:
        pairing, device_code, user_code = create_pairing_session(parse_computer_json_payload(request))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    verification_uri = f"{settings.PUBLIC_SITE_URL.rstrip('/')}/app/integrations"
    return JsonResponse(
        {
            "pairing_id": str(pairing.id),
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": (
                f"{verification_uri}?computer_pairing={pairing.id}&user_code={user_code}"
            ),
            "expires_at": pairing.expires_at.isoformat(),
            "interval": settings.COMPUTER_CPP_PAIRING_POLL_INTERVAL_SECONDS,
        },
        status=201,
    )


@csrf_exempt
@require_POST
def computer_pairing_exchange(request: HttpRequest, pairing_id):
    pairing = get_object_or_404(ComputerPairingSession, id=pairing_id)
    try:
        payload = parse_computer_json_payload(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    device_code = str(payload.get("device_code") or "")
    if not device_code:
        return HttpResponseBadRequest("device_code is required")
    if not pairing_device_code_matches(pairing, device_code):
        return JsonResponse({"error": "access_denied"}, status=403)

    now = timezone.now()
    interval = settings.COMPUTER_CPP_PAIRING_POLL_INTERVAL_SECONDS
    if pairing.last_polled_at and (now - pairing.last_polled_at).total_seconds() < interval:
        return JsonResponse({"error": "slow_down", "interval": interval + 1}, status=429)
    pairing.last_polled_at = now
    pairing.poll_count += 1
    pairing.save(update_fields=["last_polled_at", "poll_count"])

    try:
        device, refresh_token, access_token = redeem_pairing(pairing, device_code=device_code)
    except ComputerRelayError as exc:
        status = 428 if exc.code == "authorization_pending" else 410 if exc.code == "expired" else 400
        return JsonResponse(
            {"error": exc.code, "error_description": exc.message, "retryable": exc.retryable},
            status=status,
        )
    except PermissionError as exc:
        return JsonResponse({"error": "access_denied", "error_description": str(exc)}, status=403)
    return JsonResponse(
        {
            "device_id": str(device.id),
            "refresh_token": refresh_token,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": settings.COMPUTER_CPP_ACCESS_TOKEN_TTL_SECONDS,
            "relay_url": _relay_url(),
            "agent_id": str(pairing.selected_agent_id),
        }
    )


@csrf_exempt
@require_POST
def computer_token_refresh(request: HttpRequest):
    try:
        payload = parse_computer_json_payload(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    refresh_token = str(payload.get("refresh_token") or "")
    if not refresh_token:
        return HttpResponseBadRequest("refresh_token is required")
    client_version = str(payload.get("client_version") or "").strip()
    protocol_version = payload.get("protocol_version")
    if client_version and not computer_client_version_supported(client_version):
        return JsonResponse({"error": "update_required"}, status=426)
    if protocol_version not in (None, settings.COMPUTER_CPP_RELAY_PROTOCOL_VERSION):
        return JsonResponse({"error": "update_required"}, status=426)
    try:
        device, replacement, access_token = rotate_refresh_token(refresh_token)
    except ComputerRelayError as exc:
        return JsonResponse(
            {"error": exc.code, "error_description": exc.message, "retryable": exc.retryable},
            status=429 if exc.code == "rate_limited" else 400,
        )
    except PermissionError as exc:
        return JsonResponse({"error": "invalid_grant", "error_description": str(exc)}, status=401)
    update_fields = []
    if client_version and device.client_version != client_version:
        device.client_version = client_version[:32]
        update_fields.append("client_version")
    if protocol_version is not None and device.protocol_version != protocol_version:
        device.protocol_version = protocol_version
        update_fields.append("protocol_version")
    if update_fields:
        device.save(update_fields=[*update_fields, "updated_at"])
    return JsonResponse(
        {
            "device_id": str(device.id),
            "refresh_token": replacement,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": settings.COMPUTER_CPP_ACCESS_TOKEN_TTL_SECONDS,
            "relay_url": _relay_url(),
        }
    )


@csrf_exempt
@require_POST
def computer_artifact_upload(request: HttpRequest):
    authorization = str(request.headers.get("Authorization") or "")
    if not authorization.lower().startswith("bearer "):
        return JsonResponse({"error": "invalid_token"}, status=401)
    try:
        device = authenticate_relay_access_token(authorization.split(" ", 1)[1].strip())
    except PermissionError as exc:
        return JsonResponse({"error": "invalid_token", "error_description": str(exc)}, status=401)
    upload = request.FILES.get("file")
    if upload is None:
        return HttpResponseBadRequest("file is required")
    try:
        artifact = store_artifact(device, upload)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    return JsonResponse(
        {
            "artifact_id": str(artifact.id),
            "mime_type": artifact.mime_type,
            "byte_count": artifact.byte_count,
            "sha256": artifact.sha256,
            "expires_at": artifact.expires_at.isoformat(),
        },
        status=201,
    )
