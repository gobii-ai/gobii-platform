import logging

from celery.exceptions import CeleryError
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View
from kombu.exceptions import OperationalError as KombuOperationalError

from api.models import PortableAgentExport
from api.services.portable_agent_exports import (
    create_portable_agent_export,
    expire_export_artifact,
    load_download_token,
    portable_agent_export_storage,
    resolve_portable_export_scope,
    revoke_export_artifact,
    serialize_portable_agent_export,
    user_can_access_export,
)
from api.tasks.portable_agent_exports import mark_portable_agent_export_failed, process_portable_agent_export
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body
from constants.feature_flags import PORTABLE_AGENT_EXPORTS
from util.analytics import Analytics, AnalyticsEvent
from util.waffle_flags import is_waffle_flag_active


logger = logging.getLogger(__name__)


def _error(message: str, *, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _expire_if_needed(export: PortableAgentExport) -> None:
    if (
        export.status in PortableAgentExport.READY_STATUSES
        and export.expires_at
        and export.expires_at <= timezone.now()
    ):
        expire_export_artifact(export)


def _scope_queryset(request, scope: str, agent_id: str | None):
    scope_key, _agent, _organization, _agents = resolve_portable_export_scope(
        request,
        scope,
        agent_id,
        include_agents=False,
    )
    queryset = PortableAgentExport.objects.filter(requester=request.user).select_related(
        "requester", "agent", "organization",
    )
    return queryset.filter(scope_key=scope_key)


class PortableAgentExportListCreateAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        scope = str(request.GET.get("scope") or "").strip()
        agent_id = str(request.GET.get("agentId") or "").strip() or None
        try:
            queryset = _scope_queryset(request, scope, agent_id)
        except ValidationError as exc:
            return _error(exc.messages[0], status=400)
        except PermissionDenied as exc:
            return _error(str(exc), status=403)
        exports = list(queryset.order_by("-created_at")[:10])
        for export in exports:
            if not user_can_access_export(request.user, export):
                revoke_export_artifact(export)
            else:
                _expire_if_needed(export)
        return JsonResponse({"exports": [serialize_portable_agent_export(export) for export in exports]})

    def post(self, request, *args, **kwargs):
        if not is_waffle_flag_active(PORTABLE_AGENT_EXPORTS, request, default=False):
            raise Http404
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return _error(str(exc), status=400)
        scope = str(payload.get("scope") or "").strip()
        agent_id = payload.get("agentId")
        try:
            export, created = create_portable_agent_export(request, scope=scope, agent_id=agent_id)
        except ValidationError as exc:
            message = next(iter(exc.message_dict.values()))[0] if hasattr(exc, "message_dict") else exc.messages[0]
            return _error(str(message), status=400)
        except PermissionDenied as exc:
            return _error(str(exc), status=403)

        if created:
            Analytics.track(
                user_id=request.user.id,
                event=AnalyticsEvent.AGENT_PORTABLE_EXPORT_REQUESTED,
                properties={"export_id": str(export.id), "scope": export.scope, "agent_count": export.total_agents},
                user=request.user,
            )
            try:
                process_portable_agent_export.delay(str(export.id))
            except (CeleryError, KombuOperationalError, OSError, RuntimeError) as exc:
                logger.warning(
                    "Failed to queue portable export %s error=%s",
                    export.id,
                    type(exc).__name__,
                )
                mark_portable_agent_export_failed(
                    export,
                    code="queue_failed",
                    message="The export could not be queued. Please try again.",
                )
                return _error(export.error_message, status=503)
        return JsonResponse(
            {"export": serialize_portable_agent_export(export), "created": created},
            status=202,
        )


class PortableAgentExportDetailAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request, export_id, *args, **kwargs):
        export = get_object_or_404(
            PortableAgentExport.objects.select_related("requester", "agent", "organization"),
            pk=export_id,
            requester=request.user,
        )
        if not user_can_access_export(request.user, export):
            revoke_export_artifact(export)
            return _error("You no longer have permission to access this export.", status=403)
        _expire_if_needed(export)
        return JsonResponse({"export": serialize_portable_agent_export(export)})


class PortableAgentExportDownloadAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request, export_id, *args, **kwargs):
        export = get_object_or_404(
            PortableAgentExport.objects.select_related("requester", "agent", "organization"),
            pk=export_id,
            requester=request.user,
        )
        token = str(request.GET.get("token") or "")
        try:
            payload = load_download_token(
                token,
                max_age_seconds=settings.PORTABLE_AGENT_EXPORT_ARTIFACT_TTL_DAYS * 24 * 60 * 60,
            )
        except (signing.BadSignature, signing.SignatureExpired):
            return _error("This download link is invalid or expired.", status=403)
        if payload.get("exportId") != str(export.id) or payload.get("requesterId") != request.user.id:
            return _error("This download link does not belong to your account.", status=403)
        expected_expiry = int(export.expires_at.timestamp()) if export.expires_at else None
        if payload.get("expiresAt") != expected_expiry or expected_expiry is None or timezone.now().timestamp() > expected_expiry:
            return _error("This download link is invalid or expired.", status=403)
        if not user_can_access_export(request.user, export):
            revoke_export_artifact(export)
            return _error("You no longer have permission to access this export.", status=403)
        _expire_if_needed(export)
        if export.status not in PortableAgentExport.READY_STATUSES or not export.storage_key:
            return _error("This export is not available for download.", status=410)
        storage = portable_agent_export_storage()
        if not storage.exists(export.storage_key):
            return _error("The export file is no longer available.", status=410)
        response = FileResponse(
            storage.open(export.storage_key, "rb"),
            as_attachment=True,
            filename=export.archive_filename or "gobii-agent-export.zip",
            content_type="application/zip",
        )
        if export.archive_size_bytes is not None:
            response["Content-Length"] = str(export.archive_size_bytes)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
