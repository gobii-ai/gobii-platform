import logging

from botocore.exceptions import BotoCoreError, ClientError
from celery.exceptions import CeleryError
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View
from kombu.exceptions import OperationalError as KombuOperationalError

from api.models import PersistentAgent, PortableAgentExport
from api.services.organization_permissions import ORG_AGENT_CONFIG_AUTHORITY_ROLES
from api.services.portable_agent_exports import (
    create_portable_agent_export,
    load_download_token,
    revoke_export_artifact,
    serialize_portable_agent_export,
    user_can_access_export,
    user_can_export_agent,
)
from api.tasks.portable_agent_exports import process_portable_agent_export
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body
from console.context_helpers import build_console_context
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
        artifact_deleted = not export.storage_key
        if export.storage_key:
            try:
                if default_storage.exists(export.storage_key):
                    default_storage.delete(export.storage_key)
                artifact_deleted = True
            except (BotoCoreError, ClientError, OSError, ValueError):
                logger.warning("Could not delete expired portable export %s", export.id, exc_info=True)
        export.status = PortableAgentExport.Status.EXPIRED
        export.phase = "expired"
        update_fields = ["status", "phase", "updated_at"]
        if artifact_deleted:
            export.storage_key = ""
            export.archive_size_bytes = None
            export.archive_sha256 = ""
            update_fields.extend(["storage_key", "archive_size_bytes", "archive_sha256"])
        export.save(update_fields=update_fields)


def _scope_queryset(request, scope: str, agent_id: str | None):
    queryset = PortableAgentExport.objects.filter(requester=request.user).select_related(
        "requester", "agent", "organization",
    )
    if scope == PortableAgentExport.Scope.PERSONAL:
        return queryset.filter(scope_key=f"personal:{request.user.id}")
    if scope == PortableAgentExport.Scope.AGENT:
        if not agent_id:
            raise ValidationError({"agentId": "agentId is required."})
        agent = PersistentAgent.objects.non_eval().alive().filter(pk=agent_id).first()
        if agent is None or not user_can_export_agent(request.user, agent):
            raise PermissionDenied("You do not have permission to view exports for this agent.")
        return queryset.filter(scope_key=f"agent:{agent.id}")
    if scope == PortableAgentExport.Scope.ORGANIZATION:
        context = build_console_context(request)
        membership = context.current_membership
        if context.current_context.type != "organization" or membership is None:
            raise ValidationError({"scope": "Switch to an organization first."})
        if membership.role not in ORG_AGENT_CONFIG_AUTHORITY_ROLES:
            raise PermissionDenied("You do not have permission to view this organization's exports.")
        return queryset.filter(scope_key=f"organization:{membership.org_id}")
    raise ValidationError({"scope": "scope must be agent, personal, or organization."})


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
            except (CeleryError, KombuOperationalError, OSError, RuntimeError):
                logger.exception("Failed to queue portable export %s", export.id)
                export.status = PortableAgentExport.Status.FAILED
                export.phase = "failed"
                export.error_code = "queue_failed"
                export.error_message = "The export could not be queued. Please try again."
                export.completed_at = timezone.now()
                export.save(update_fields=[
                    "status", "phase", "error_code", "error_message", "completed_at", "updated_at",
                ])
                Analytics.track(
                    user_id=request.user.id,
                    event=AnalyticsEvent.AGENT_PORTABLE_EXPORT_FAILED,
                    properties={
                        "export_id": str(export.id),
                        "scope": export.scope,
                        "error_code": "queue_failed",
                        "agent_count": export.total_agents,
                    },
                    user=request.user,
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
        if not default_storage.exists(export.storage_key):
            return _error("The export file is no longer available.", status=410)
        Analytics.track(
            user_id=request.user.id,
            event=AnalyticsEvent.AGENT_PORTABLE_EXPORT_DOWNLOADED,
            properties={
                "export_id": str(export.id),
                "scope": export.scope,
                "agent_count": export.completed_agents,
                "warning_count": export.warning_count,
                "redaction_count": export.redaction_count,
                "archive_size_bytes": export.archive_size_bytes,
            },
            user=request.user,
        )
        response = FileResponse(
            default_storage.open(export.storage_key, "rb"),
            as_attachment=True,
            filename=export.archive_filename or "gobii-agent-export.zip",
            content_type="application/zip",
        )
        if export.archive_size_bytes is not None:
            response["Content-Length"] = str(export.archive_size_bytes)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
