import re
from decimal import Decimal
from typing import Any

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from api.models import (
    PersistentAgentStep,
    PersistentAgentWorkPlan,
    PersistentAgentWorkPlanStep,
)

DECIMAL_ZERO = Decimal("0")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_work_plan_step_key(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip().lower())


def _decimal_total_expression():
    return Coalesce(
        Sum("credits_cost"),
        Value(DECIMAL_ZERO, output_field=DecimalField(max_digits=20, decimal_places=6)),
    )


def decimal_to_float(value: Any) -> float:
    try:
        return float(value or DECIMAL_ZERO)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def iso_value(value: Any) -> str | None:
    return value.isoformat() if value else None


def get_active_work_plan(agent) -> PersistentAgentWorkPlan | None:
    if not agent or not getattr(agent, "id", None):
        return None
    return (
        PersistentAgentWorkPlan.objects.filter(
            agent=agent,
            status=PersistentAgentWorkPlan.Status.ACTIVE,
        )
        .order_by("-started_at")
        .first()
    )


def get_display_work_plan(agent) -> PersistentAgentWorkPlan | None:
    active_plan = get_active_work_plan(agent)
    if active_plan is not None:
        return active_plan
    if not agent or not getattr(agent, "id", None):
        return None
    return (
        PersistentAgentWorkPlan.objects.filter(agent=agent)
        .order_by("-started_at")
        .first()
    )


def resolve_work_plan_for_update(agent, plan_items: list[dict[str, str]]) -> PersistentAgentWorkPlan:
    now = timezone.now()
    incoming_keys = {
        normalize_work_plan_step_key(item.get("step") or "")
        for item in plan_items
        if item.get("step")
    }
    active_plan = (
        PersistentAgentWorkPlan.objects.select_for_update()
        .filter(agent=agent, status=PersistentAgentWorkPlan.Status.ACTIVE)
        .order_by("-started_at")
        .first()
    )

    if active_plan is not None:
        active_keys = set(
            active_plan.steps.filter(archived_at__isnull=True).values_list("normalized_title", flat=True)
        )
        if not active_keys or active_keys.intersection(incoming_keys):
            return active_plan

        active_plan.status = PersistentAgentWorkPlan.Status.SUPERSEDED
        active_plan.superseded_at = now
        active_plan.save(update_fields=["status", "superseded_at", "updated_at"])

    title = ""
    if plan_items:
        title = (plan_items[0].get("step") or "").strip()[:255]
    return PersistentAgentWorkPlan.objects.create(
        agent=agent,
        title=title,
        status=PersistentAgentWorkPlan.Status.ACTIVE,
        started_at=now,
    )


def sync_work_plan_steps(
    *,
    work_plan: PersistentAgentWorkPlan,
    plan_items: list[dict[str, str]],
    will_continue_work: bool | None,
) -> None:
    now = timezone.now()
    existing_steps = list(
        work_plan.steps.select_for_update()
        .filter(archived_at__isnull=True)
        .order_by("position", "created_at")
    )
    existing_by_key: dict[str, list[PersistentAgentWorkPlanStep]] = {}
    for step in existing_steps:
        existing_by_key.setdefault(step.normalized_title, []).append(step)

    matched_step_ids: set[str] = set()
    for index, item in enumerate(plan_items):
        title = (item.get("step") or "").strip()[:255]
        status = item.get("status") or PersistentAgentWorkPlanStep.Status.TODO
        key = normalize_work_plan_step_key(title)
        matching = existing_by_key.get(key)
        step = matching.pop(0) if matching else None

        if step is None:
            step = PersistentAgentWorkPlanStep(
                work_plan=work_plan,
                title=title,
                normalized_title=key,
            )

        matched_step_ids.add(str(step.id))
        update_fields: list[str] = []
        if step.title != title:
            step.title = title
            update_fields.append("title")
        if step.normalized_title != key:
            step.normalized_title = key
            update_fields.append("normalized_title")
        if step.status != status:
            step.status = status
            update_fields.append("status")
        if step.position != index:
            step.position = index
            update_fields.append("position")
        if status == PersistentAgentWorkPlanStep.Status.DOING and step.started_at is None:
            step.started_at = now
            update_fields.append("started_at")
        if status == PersistentAgentWorkPlanStep.Status.DONE:
            if step.completed_at is None:
                step.completed_at = now
                update_fields.append("completed_at")
        elif step.completed_at is not None:
            step.completed_at = None
            update_fields.append("completed_at")

        if step._state.adding:
            step.save()
        elif update_fields:
            update_fields.append("updated_at")
            step.save(update_fields=list(dict.fromkeys(update_fields)))

    for step in existing_steps:
        if str(step.id) in matched_step_ids:
            continue
        step.archived_at = now
        step.save(update_fields=["archived_at", "updated_at"])

    active_statuses = {
        PersistentAgentWorkPlanStep.Status.TODO,
        PersistentAgentWorkPlanStep.Status.DOING,
    }
    has_active_steps = any((item.get("status") or "") in active_statuses for item in plan_items)
    if will_continue_work is False and not has_active_steps:
        work_plan.status = PersistentAgentWorkPlan.Status.COMPLETED
        work_plan.completed_at = now
        work_plan.save(update_fields=["status", "completed_at", "updated_at"])


def attach_active_work_plan_to_step(step: PersistentAgentStep) -> None:
    agent = getattr(step, "agent", None)
    work_plan = get_active_work_plan(agent)
    if work_plan is None:
        return
    current_step = (
        work_plan.steps.filter(
            archived_at__isnull=True,
            status=PersistentAgentWorkPlanStep.Status.DOING,
        )
        .order_by("position", "created_at")
        .first()
    )
    step.work_plan = work_plan
    if current_step is not None:
        step.work_plan_step = current_step


def credit_total_for_work_plan(work_plan: PersistentAgentWorkPlan | None) -> Decimal:
    if work_plan is None:
        return DECIMAL_ZERO
    return (
        PersistentAgentStep.objects.filter(work_plan=work_plan, credits_cost__isnull=False)
        .aggregate(total=_decimal_total_expression())
        .get("total")
        or DECIMAL_ZERO
    )


def credit_totals_for_work_plan_steps(work_plan: PersistentAgentWorkPlan | None) -> dict[str, Decimal]:
    if work_plan is None:
        return {}
    rows = (
        PersistentAgentStep.objects.filter(
            work_plan=work_plan,
            work_plan_step__isnull=False,
            credits_cost__isnull=False,
        )
        .values("work_plan_step_id")
        .annotate(total=_decimal_total_expression())
    )
    return {str(row["work_plan_step_id"]): row.get("total") or DECIMAL_ZERO for row in rows}


def serialize_work_plan_step_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "title": item["title"],
        "status": item["status"],
        "creditsUsed": decimal_to_float(item.get("credits_used")),
        "startedAt": iso_value(item.get("started_at")),
        "completedAt": iso_value(item.get("completed_at")),
    }


def serialize_work_plan_step_model(
    step: PersistentAgentWorkPlanStep,
    *,
    credits_used: Any = DECIMAL_ZERO,
) -> dict[str, Any]:
    return {
        "id": str(step.id),
        "title": step.title,
        "status": step.status,
        "creditsUsed": decimal_to_float(credits_used),
        "startedAt": iso_value(step.started_at),
        "completedAt": iso_value(step.completed_at),
    }


def serialize_work_plan_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    work_plan = snapshot.get("work_plan")
    if work_plan is None:
        return None
    steps = [serialize_work_plan_step_item(item) for item in snapshot.get("steps", [])]
    return {
        "id": str(work_plan.id),
        "status": work_plan.status,
        "title": work_plan.title,
        "startedAt": iso_value(work_plan.started_at),
        "completedAt": iso_value(work_plan.completed_at),
        "creditsUsed": decimal_to_float(snapshot.get("total_credits")),
        "steps": steps,
    }


def serialize_current_work_plan_step_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for item in snapshot.get("steps", []):
        if item.get("status") == PersistentAgentWorkPlanStep.Status.DOING:
            return serialize_work_plan_step_item(item)
    return None


def build_work_plan_credit_snapshot(agent) -> dict[str, Any]:
    work_plan = get_display_work_plan(agent)
    if work_plan is None:
        return {
            "work_plan": None,
            "steps": [],
            "total_credits": DECIMAL_ZERO,
            "current_step_credits": DECIMAL_ZERO,
        }

    step_totals = credit_totals_for_work_plan_steps(work_plan)
    plan_total = credit_total_for_work_plan(work_plan)
    steps = list(work_plan.steps.filter(archived_at__isnull=True).order_by("position", "created_at"))
    current_step_credits = DECIMAL_ZERO
    serialized_steps = []
    for step in steps:
        credits_used = step_totals.get(str(step.id), DECIMAL_ZERO)
        if step.status == PersistentAgentWorkPlanStep.Status.DOING:
            current_step_credits = credits_used
        serialized_steps.append(
            {
                "id": str(step.id),
                "title": step.title,
                "status": step.status,
                "credits_used": credits_used,
                "started_at": step.started_at,
                "completed_at": step.completed_at,
            }
        )

    return {
        "work_plan": work_plan,
        "steps": serialized_steps,
        "total_credits": plan_total,
        "current_step_credits": current_step_credits,
    }
