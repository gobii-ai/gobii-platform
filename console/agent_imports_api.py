import logging

from celery.exceptions import CeleryError
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View
from kombu.exceptions import OperationalError as KombuOperationalError

from api.models import PortableAgentImport
from api.services.portable_agent_imports import (
    create_portable_agent_import,
    discard_portable_agent_import,
    expire_portable_agent_import,
    portable_agent_imports_enabled,
    reserve_portable_agent_shells,
    resolve_portable_import_target,
    serialize_portable_agent_import,
    user_can_import_to_target,
)
from api.tasks.portable_agent_imports import (
    mark_portable_agent_import_failed,
    process_portable_agent_import,
    validate_portable_agent_import,
)
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body


logger = logging.getLogger(__name__)


def _error(message: str, *, status=400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return str(next(iter(exc.message_dict.values()))[0])
    return str(exc.messages[0])


def _require_feature(request) -> None:
    if not portable_agent_imports_enabled(request):
        raise Http404


def _expire_if_needed(job: PortableAgentImport) -> None:
    if (
        job.status in {
            PortableAgentImport.Status.VALIDATING,
            PortableAgentImport.Status.AWAITING_SELECTION,
        }
        and job.expires_at
        and job.expires_at <= timezone.now()
    ):
        expire_portable_agent_import(job)


def _get_requester_job(request, import_id) -> PortableAgentImport:
    job = get_object_or_404(
        PortableAgentImport.objects.select_related("requester", "organization"),
        pk=import_id,
        requester=request.user,
    )
    _expire_if_needed(job)
    return job


class PortableAgentImportListCreateAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        _require_feature(request)
        try:
            _target_type, target_key, _organization = resolve_portable_import_target(request)
        except PermissionDenied as exc:
            return _error(str(exc), status=403)
        jobs = list(
            PortableAgentImport.objects.filter(
                requester=request.user,
                target_key=target_key,
            ).select_related("requester", "organization").order_by("-created_at")[:10]
        )
        for job in jobs:
            _expire_if_needed(job)
        return JsonResponse({
            "imports": [
                serialize_portable_agent_import(job, include_agents=False)
                for job in jobs
            ],
        })

    def post(self, request, *args, **kwargs):
        _require_feature(request)
        uploaded_file = request.FILES.get("archive")
        if uploaded_file is None:
            return _error("Choose a Gobii portable-export ZIP file.")
        try:
            job = create_portable_agent_import(request, uploaded_file)
        except PermissionDenied as exc:
            return _error(str(exc), status=403)
        except ValidationError as exc:
            return _error(_validation_message(exc))
        try:
            validate_portable_agent_import.delay(str(job.id))
        except (CeleryError, KombuOperationalError, OSError, RuntimeError) as exc:
            logger.warning("Failed to queue portable import validation import=%s error=%s", job.id, type(exc).__name__)
            mark_portable_agent_import_failed(
                job,
                code="queue_failed",
                message="The upload could not be queued for validation. Please try again.",
            )
            return _error(job.error_message, status=503)
        return JsonResponse({"import": serialize_portable_agent_import(job)}, status=202)


class PortableAgentImportDetailAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "delete"]

    def get(self, request, import_id, *args, **kwargs):
        _require_feature(request)
        job = _get_requester_job(request, import_id)
        if not user_can_import_to_target(request.user, job) and job.status not in PortableAgentImport.TERMINAL_STATUSES:
            return _error("You no longer have permission to access this import.", status=403)
        return JsonResponse({"import": serialize_portable_agent_import(job)})

    def delete(self, request, import_id, *args, **kwargs):
        _require_feature(request)
        job = _get_requester_job(request, import_id)
        if not user_can_import_to_target(request.user, job):
            return _error("You no longer have permission to discard this import.", status=403)
        try:
            discard_portable_agent_import(job)
        except ValidationError as exc:
            return _error(_validation_message(exc), status=409)
        return JsonResponse({}, status=204)


class PortableAgentImportStartAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, import_id, *args, **kwargs):
        _require_feature(request)
        job = _get_requester_job(request, import_id)
        if not user_can_import_to_target(request.user, job):
            return _error("You no longer have permission to start this import.", status=403)
        try:
            payload = _parse_json_body(request)
            created = reserve_portable_agent_shells(job, payload.get("agents"))
        except ValueError as exc:
            return _error(str(exc))
        except PermissionDenied as exc:
            return _error(str(exc), status=403)
        except ValidationError as exc:
            return _error(_validation_message(exc), status=409)
        job.refresh_from_db()
        if created:
            try:
                process_portable_agent_import.delay(str(job.id))
            except (CeleryError, KombuOperationalError, OSError, RuntimeError) as exc:
                logger.warning("Failed to queue portable import import=%s error=%s", job.id, type(exc).__name__)
                mark_portable_agent_import_failed(
                    job,
                    code="queue_failed",
                    message="The import could not be queued. Please upload the export again.",
                    fail_selected_items=True,
                )
                return _error(job.error_message, status=503)
        return JsonResponse(
            {"import": serialize_portable_agent_import(job), "created": created},
            status=202 if created else 200,
        )
