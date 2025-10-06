from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views import View

from billing.services import BillingService

from api.models import BrowserUseAgentTask, TaskCredit
from console.context_helpers import build_console_context


def _format_period_label(start_date, end_date) -> str:
    """Return a concise date range label such as 'Jul 1 – Jul 31, 2024'."""

    start_month = start_date.strftime("%b")
    end_month = end_date.strftime("%b")

    if start_date.year == end_date.year:
        start_label = f"{start_month} {start_date.day}"
        end_label = f"{end_month} {end_date.day}, {end_date.year}"
    else:
        start_label = f"{start_month} {start_date.day}, {start_date.year}"
        end_label = f"{end_month} {end_date.day}, {end_date.year}"

    return f"{start_label} – {end_label}"


class UsageSummaryAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        resolved = build_console_context(request)

        owner = request.user
        owner_context_type = "personal"
        organization = None

        if resolved.current_context.type == "organization" and resolved.current_membership:
            organization = resolved.current_membership.org
            owner = organization
            owner_context_type = "organization"

        def _parse_query_date(value: str | None) -> date | None:
            if not value:
                return None
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                return None

        requested_start = _parse_query_date(request.GET.get("from"))
        requested_end = _parse_query_date(request.GET.get("to"))

        if requested_start and requested_end and requested_start <= requested_end:
            period_start, period_end = requested_start, requested_end
        else:
            period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)

        tz = timezone.get_current_timezone()
        period_start_dt = timezone.make_aware(datetime.combine(period_start, time.min), tz)
        period_end_dt = timezone.make_aware(datetime.combine(period_end, time.max), tz)

        filters = {
            "is_deleted": False,
            "created_at__gte": period_start_dt,
            "created_at__lte": period_end_dt,
        }

        if organization is not None:
            filters["organization"] = organization
        else:
            filters["user"] = request.user

        tasks_qs = BrowserUseAgentTask.objects.filter(**filters)

        status_counts = {status: 0 for status in BrowserUseAgentTask.StatusChoices.values}
        for row in tasks_qs.values("status").annotate(count=Count("status")):
            status_counts[row["status"]] = row["count"]

        tasks_total = tasks_qs.count()

        credits_zero = Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=6))
        credits_agg = tasks_qs.aggregate(total=Coalesce(Sum("credits_cost"), credits_zero))
        total_credits = credits_agg.get("total") or Decimal("0")

        now = timezone.now()
        credit_filters = {
            "granted_date__lte": now,
            "expiration_date__gte": now,
            "voided": False,
        }
        if organization is not None:
            credit_filters["organization"] = organization
        else:
            credit_filters["user"] = request.user

        credit_agg = TaskCredit.objects.filter(**credit_filters).aggregate(
            available=Coalesce(Sum("available_credits"), credits_zero),
            total=Coalesce(Sum("credits"), credits_zero),
            used=Coalesce(Sum("credits_used"), credits_zero),
        )

        available_credits = credit_agg.get("available") or Decimal("0")
        quota_total = credit_agg.get("total") or Decimal("0")
        quota_used = credit_agg.get("used") or Decimal("0")

        quota_used_pct = 0.0
        if quota_total > 0:
            quota_used_pct = float((quota_used / quota_total) * Decimal("100"))

        payload = {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
                "label": _format_period_label(period_start, period_end),
                "timezone": timezone.get_current_timezone_name(),
            },
            "context": {
                "type": owner_context_type,
                "id": resolved.current_context.id,
                "name": resolved.current_context.name,
            },
            "metrics": {
                "tasks": {
                    "count": tasks_total,
                    "completed": status_counts.get(BrowserUseAgentTask.StatusChoices.COMPLETED, 0),
                    "in_progress": status_counts.get(BrowserUseAgentTask.StatusChoices.IN_PROGRESS, 0),
                    "pending": status_counts.get(BrowserUseAgentTask.StatusChoices.PENDING, 0),
                    "failed": status_counts.get(BrowserUseAgentTask.StatusChoices.FAILED, 0),
                    "cancelled": status_counts.get(BrowserUseAgentTask.StatusChoices.CANCELLED, 0),
                },
                "credits": {
                    "total": float(total_credits),
                    "unit": "credits",
                },
                "quota": {
                    "available": float(available_credits),
                    "total": float(quota_total),
                    "used": float(quota_used),
                    "used_pct": quota_used_pct,
                },
            },
        }

        return JsonResponse(payload)
