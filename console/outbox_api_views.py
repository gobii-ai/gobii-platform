import base64
import json
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views import View

from api.agent.comms.email_threading import get_message_contact_address
from api.models import (
    DeliveryStatus,
    Organization,
    OutboundEmailReview,
    PersistentAgent,
    UserPreference,
)
from api.services.outbound_email_policy import (
    classify_email_recipients,
    email_review_outbox_enabled,
    get_effective_email_sending_mode,
    get_organization_minimum_email_sending_mode,
    get_workspace_default_email_sending_mode,
    set_workspace_email_sending_policy,
)
from api.services.outbound_email_review import (
    NON_RETRYABLE_OUTBOX_ERROR_CODES,
    OutboundEmailReviewError,
    StaleOutboxVersionError,
    approve_review,
    discard_review,
    expire_review_if_needed,
    retry_review,
    review_is_stale,
    review_thread_changed,
    update_pending_review_message,
)
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body
from console.context_helpers import resolve_console_context
from console.context_overrides import get_context_override


PAGE_SIZE = 30
MAX_PAGE_SIZE = 100
EDITABLE_FIELDS = {"to", "cc", "subject", "body", "attachmentNodeIds"}


def _payload(request: HttpRequest) -> dict[str, Any]:
    try:
        return _parse_json_body(request)
    except ValueError as exc:
        raise OutboundEmailReviewError(str(exc)) from exc


def _workspace(request: HttpRequest):
    try:
        context_info = resolve_console_context(
            request.user,
            request.session,
            override=get_context_override(request),
        )
    except PermissionDenied as exc:
        raise OutboundEmailReviewError("Not permitted.") from exc
    context = context_info.current_context
    if context.type == "organization":
        if not context_info.can_manage_org_agents:
            raise OutboundEmailReviewError("You do not have permission to review this workspace's Outbox.")
        organization = Organization.objects.get(pk=context.id)
        agents = PersistentAgent.objects.filter(organization=organization, is_deleted=False)
        return context_info, organization, agents
    agents = PersistentAgent.objects.filter(user=request.user, organization__isnull=True, is_deleted=False)
    return context_info, None, agents


def _workspace_reviews(request: HttpRequest):
    _, _, agents = _workspace(request)
    return OutboundEmailReview.objects.filter(agent__in=agents).select_related(
        "agent",
        "message__from_endpoint",
        "message__to_endpoint",
        "message__conversation",
        "decided_by",
        "last_edited_by",
    ).prefetch_related("message__cc_endpoints", "message__attachments")


def _display_status(review: OutboundEmailReview) -> str:
    delivery_status = review.message.latest_status
    if review.status == OutboundEmailReview.Status.PENDING:
        return "needs_review"
    if review.status in {OutboundEmailReview.Status.DISCARDED, OutboundEmailReview.Status.EXPIRED}:
        return review.status
    if delivery_status in {DeliveryStatus.QUEUED, DeliveryStatus.SENDING}:
        return "sending"
    if delivery_status == DeliveryStatus.FAILED:
        return "failed"
    return "sent"


def _warnings(review: OutboundEmailReview) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    message = review.message
    if review.status == OutboundEmailReview.Status.PENDING:
        recipients = [get_message_contact_address(message), *message.cc_endpoints.values_list("address", flat=True)]
        decision = classify_email_recipients(review.agent, recipients)
        if decision.unknown_external_recipients:
            warnings.append({"code": "new_contact", "label": "New contact"})
        cc_decision = classify_email_recipients(
            review.agent,
            message.cc_endpoints.values_list("address", flat=True),
        )
        if cc_decision.external_recipients:
            warnings.append({"code": "external_cc", "label": "External CC"})
    if review_is_stale(review):
        warnings.append({"code": "stale", "label": "Stale"})
    if review_thread_changed(review):
        warnings.append({"code": "conversation_changed", "label": "Conversation changed"})
    return warnings


def serialize_outbox_review(review: OutboundEmailReview, *, detail: bool = False) -> dict[str, Any]:
    expire_review_if_needed(review)
    message = review.message
    raw_payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
    to_address = get_message_contact_address(message)
    cc_addresses = list(message.cc_endpoints.values_list("address", flat=True))
    allowed_actions = {
        "edit": review.status == OutboundEmailReview.Status.PENDING,
        "approve": review.status == OutboundEmailReview.Status.PENDING,
        "discard": review.status == OutboundEmailReview.Status.PENDING,
        "retry": (
            review.status == OutboundEmailReview.Status.APPROVED
            and message.latest_status == DeliveryStatus.FAILED
            and message.latest_error_code not in NON_RETRYABLE_OUTBOX_ERROR_CODES
        ),
    }
    payload: dict[str, Any] = {
        "id": str(review.id),
        "messageId": str(message.id),
        "agent": {"id": str(review.agent_id), "name": review.agent.name},
        "sender": message.from_endpoint.address,
        "to": to_address,
        "cc": cc_addresses,
        "subject": str(raw_payload.get("subject") or ""),
        "bodyPreview": (message.body or "")[:240],
        "status": _display_status(review),
        "reviewStatus": review.status,
        "deliveryStatus": message.latest_status,
        "version": review.content_version,
        "queuedAt": review.queued_at.isoformat(),
        "expiresAt": review.expires_at.isoformat(),
        "decidedAt": review.decided_at.isoformat() if review.decided_at else None,
        "warnings": _warnings(review),
        "allowedActions": allowed_actions,
        "lastError": message.latest_error_message or None,
    }
    if detail:
        thread_messages = []
        if message.conversation_id:
            thread_messages = [
                {
                    "id": str(item.id),
                    "body": item.body,
                    "isOutbound": item.is_outbound,
                    "timestamp": item.timestamp.isoformat(),
                }
                for item in message.conversation.messages.exclude(pk=message.pk).order_by("-timestamp")[:5]
            ]
        payload.update(
            {
                "bodyHtml": message.body,
                "attachments": [
                    {
                        "id": str(attachment.id),
                        "nodeId": str(attachment.filespace_node_id) if attachment.filespace_node_id else None,
                        "filename": attachment.filename,
                        "contentType": attachment.content_type,
                        "size": attachment.file_size,
                        "sha256": attachment.content_sha256,
                    }
                    for attachment in message.attachments.all()
                ],
                "threadContext": thread_messages,
                "lastEditedAt": review.last_edited_at.isoformat() if review.last_edited_at else None,
                "lastEditedBy": review.last_edited_by.get_full_name() if review.last_edited_by else None,
            }
        )
    return payload


def _stale_response(review: OutboundEmailReview) -> JsonResponse:
    review.refresh_from_db()
    return JsonResponse(
        {"error": "stale_version", "item": serialize_outbox_review(review, detail=True)},
        status=409,
    )


def _decode_cursor(raw_cursor: str) -> tuple[str, str] | None:
    if not raw_cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("ascii")).decode("utf-8")
        queued_at, review_id = decoded.split("|", 1)
        return queued_at, review_id
    except (ValueError, UnicodeDecodeError):
        return None


def _encode_cursor(review: OutboundEmailReview) -> str:
    raw = f"{review.queued_at.isoformat()}|{review.id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


class OutboxListAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            queryset = _workspace_reviews(request)
        except OutboundEmailReviewError as exc:
            return JsonResponse({"error": str(exc)}, status=403)

        now = timezone.now()
        for expired_review in queryset.filter(
            status=OutboundEmailReview.Status.PENDING,
            expires_at__lte=now,
        )[:500]:
            expire_review_if_needed(expired_review, now=now)
        queryset = _workspace_reviews(request)
        feature_enabled = email_review_outbox_enabled()
        available = feature_enabled or queryset.exists()
        recent_filter = (
            Q(status__in=[OutboundEmailReview.Status.DISCARDED, OutboundEmailReview.Status.EXPIRED])
            | Q(
                status=OutboundEmailReview.Status.APPROVED,
                message__latest_status__in=[DeliveryStatus.SENT, DeliveryStatus.DELIVERED],
            )
        )
        counts = {
            "needsReview": queryset.filter(status=OutboundEmailReview.Status.PENDING).count(),
            "sending": queryset.filter(
                status=OutboundEmailReview.Status.APPROVED,
                message__latest_status__in=[DeliveryStatus.QUEUED, DeliveryStatus.SENDING],
            ).count(),
            "failed": queryset.filter(
                status=OutboundEmailReview.Status.APPROVED,
                message__latest_status=DeliveryStatus.FAILED,
            ).count(),
            "recent": queryset.filter(recent_filter).count(),
        }
        status_filter = request.GET.get("status", "needs_review")
        if status_filter == "needs_review":
            queryset = queryset.filter(status=OutboundEmailReview.Status.PENDING)
        elif status_filter == "sending":
            queryset = queryset.filter(status=OutboundEmailReview.Status.APPROVED, message__latest_status__in=[DeliveryStatus.QUEUED, DeliveryStatus.SENDING])
        elif status_filter == "failed":
            queryset = queryset.filter(status=OutboundEmailReview.Status.APPROVED, message__latest_status=DeliveryStatus.FAILED)
        elif status_filter == "recent":
            queryset = queryset.filter(recent_filter)
        elif status_filter != "all":
            return JsonResponse({"error": "Invalid status filter."}, status=400)

        if agent_id := request.GET.get("agent"):
            queryset = queryset.filter(agent_id=agent_id)
        if search := request.GET.get("search", "").strip():
            queryset = queryset.filter(
                Q(message__raw_payload__subject__icontains=search)
                | Q(message__conversation__address__icontains=search)
                | Q(message__to_endpoint__address__icontains=search)
                | Q(message__cc_endpoints__address__icontains=search)
            ).distinct()

        cursor = _decode_cursor(request.GET.get("cursor", ""))
        if request.GET.get("cursor") and cursor is None:
            return JsonResponse({"error": "Invalid cursor."}, status=400)
        if cursor:
            queued_at, review_id = cursor
            queryset = queryset.filter(Q(queued_at__lt=queued_at) | Q(queued_at=queued_at, id__lt=review_id))
        try:
            page_size = min(max(int(request.GET.get("limit", PAGE_SIZE)), 1), MAX_PAGE_SIZE)
        except ValueError:
            return JsonResponse({"error": "Invalid limit."}, status=400)
        page = list(queryset.order_by("-queued_at", "-id")[: page_size + 1])
        has_more = len(page) > page_size
        page = page[:page_size]
        return JsonResponse(
            {
                "featureEnabled": feature_enabled,
                "available": available,
                "items": [serialize_outbox_review(review) for review in page],
                "counts": counts,
                "nextCursor": _encode_cursor(page[-1]) if has_more and page else None,
            }
        )


class OutboxDetailAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "patch"]

    def _get_review(self, request: HttpRequest, outbox_id):
        return _workspace_reviews(request).filter(pk=outbox_id).first()

    def get(self, request: HttpRequest, outbox_id, *args: Any, **kwargs: Any):
        try:
            review = self._get_review(request, outbox_id)
        except OutboundEmailReviewError as exc:
            return JsonResponse({"error": str(exc)}, status=403)
        if review is None:
            return JsonResponse({"error": "Outbox item not found."}, status=404)
        return JsonResponse({"item": serialize_outbox_review(review, detail=True)})

    def patch(self, request: HttpRequest, outbox_id, *args: Any, **kwargs: Any):
        try:
            review = self._get_review(request, outbox_id)
            if review is None:
                return JsonResponse({"error": "Outbox item not found."}, status=404)
            payload = _payload(request)
            expected_version = int(payload.get("expectedVersion"))
            changes = {key: value for key, value in payload.items() if key in EDITABLE_FIELDS}
            if not changes:
                raise OutboundEmailReviewError("No editable fields were provided.")
            review = update_pending_review_message(
                review,
                actor=request.user,
                expected_version=expected_version,
                changes=changes,
            )
            return JsonResponse({"item": serialize_outbox_review(review, detail=True)})
        except StaleOutboxVersionError:
            return _stale_response(review)
        except (OutboundEmailReviewError, TypeError, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)


class OutboxDecisionAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]
    action = ""

    def post(self, request: HttpRequest, outbox_id, *args: Any, **kwargs: Any):
        try:
            review = _workspace_reviews(request).filter(pk=outbox_id).first()
            if review is None:
                return JsonResponse({"error": "Outbox item not found."}, status=404)
            payload = _payload(request)
            if self.action == "approve":
                changes = {key: value for key, value in payload.items() if key in EDITABLE_FIELDS}
                review = approve_review(
                    review,
                    actor=request.user,
                    expected_version=int(payload.get("expectedVersion")),
                    changes=changes,
                    acknowledge_thread_changed=payload.get("acknowledgeThreadChanged") is True,
                )
            elif self.action == "discard":
                review = discard_review(
                    review,
                    actor=request.user,
                    expected_version=int(payload.get("expectedVersion")),
                )
            else:
                review = retry_review(review, actor=request.user)
            return JsonResponse({"item": serialize_outbox_review(review, detail=True)})
        except StaleOutboxVersionError:
            return _stale_response(review)
        except (OutboundEmailReviewError, TypeError, ValueError) as exc:
            code = "thread_changed" if str(exc) == "thread_changed" else "invalid_request"
            return JsonResponse({"error": code, "message": str(exc)}, status=409 if code == "thread_changed" else 400)


class OutboxApproveAPIView(OutboxDecisionAPIView):
    action = "approve"


class OutboxDiscardAPIView(OutboxDecisionAPIView):
    action = "discard"


class OutboxRetryAPIView(OutboxDecisionAPIView):
    action = "retry"


class OutboxBulkDiscardAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            payload = _payload(request)
            items = payload.get("items")
            if not isinstance(items, list) or not items:
                raise OutboundEmailReviewError("items must be a non-empty array.")
            workspace_reviews = _workspace_reviews(request)
            discarded_ids = []
            with transaction.atomic():
                for item in items:
                    if not isinstance(item, dict):
                        raise OutboundEmailReviewError("Each item must include id and expectedVersion.")
                    review = workspace_reviews.filter(pk=item.get("id")).first()
                    if review is None:
                        raise OutboundEmailReviewError("One or more Outbox items were not found.")
                    discard_review(review, actor=request.user, expected_version=int(item.get("expectedVersion")))
                    discarded_ids.append(str(review.id))
            return JsonResponse({"discardedIds": discarded_ids})
        except StaleOutboxVersionError:
            return JsonResponse({"error": "stale_version"}, status=409)
        except (OutboundEmailReviewError, TypeError, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)


class EmailSendingPolicyAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "patch"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            context_info, organization, agents = _workspace(request)
        except OutboundEmailReviewError as exc:
            return JsonResponse({"error": str(exc)}, status=403)
        preferences = UserPreference.resolve_known_preferences(request.user)
        return JsonResponse(
            {
                "defaultMode": get_workspace_default_email_sending_mode(user=request.user, organization=organization),
                "minimumMode": get_organization_minimum_email_sending_mode(organization),
                "canSetMinimum": organization is not None and context_info.can_manage_org_agents,
                "emailNotificationsEnabled": preferences[UserPreference.KEY_OUTBOX_EMAIL_NOTIFICATIONS_ENABLED],
                "agents": [
                    {
                        "id": str(agent.id),
                        "name": agent.name,
                        "requestedMode": agent.email_sending_mode,
                        "effectiveMode": get_effective_email_sending_mode(agent),
                    }
                    for agent in agents.order_by("name")
                ],
            }
        )

    def patch(self, request: HttpRequest, *args: Any, **kwargs: Any):
        try:
            context_info, organization, _ = _workspace(request)
            payload = _payload(request)
            if "emailNotificationsEnabled" in payload:
                UserPreference.update_known_preferences(
                    request.user,
                    {UserPreference.KEY_OUTBOX_EMAIL_NOTIFICATIONS_ENABLED: payload["emailNotificationsEnabled"]},
                )
            if "defaultMode" in payload or "minimumMode" in payload or "applyToExisting" in payload:
                default_mode = payload.get("defaultMode") or get_workspace_default_email_sending_mode(
                    user=request.user,
                    organization=organization,
                )
                minimum_mode = (
                    payload.get("minimumMode")
                    if organization is not None and "minimumMode" in payload
                    else get_organization_minimum_email_sending_mode(organization)
                )
                set_workspace_email_sending_policy(
                    user=request.user,
                    organization=organization,
                    default_mode=default_mode,
                    minimum_mode=minimum_mode,
                    apply_to_existing=payload.get("applyToExisting") is True,
                )
            return self.get(request)
        except (OutboundEmailReviewError, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)
