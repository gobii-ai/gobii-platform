"""Plan update tool for persistent agents."""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from api.models import PersistentAgentKanbanCard, PersistentAgentMessage, PersistentAgentPlanDeliverable

logger = logging.getLogger(__name__)

PLAN_TOOL_NAME = "update_plan"
PLAN_STATUSES = {
    PersistentAgentKanbanCard.Status.TODO,
    PersistentAgentKanbanCard.Status.DOING,
    PersistentAgentKanbanCard.Status.DONE,
}
_WHITESPACE_RE = re.compile(r"\s+")
_RESEARCH_PLAN_TERMS = (
    "competitor",
    "current",
    "investment",
    "memo",
    "research",
    "report",
    "source",
    "synthesize",
)
MESSAGE_DELIVERABLE_GUIDANCE = (
    "Only substantial final deliveries belong here. Use the exact message_id from a user-facing send tool, never a peer message. "
    "If a final plan update must follow delivery, send with will_continue_work=true, then update the plan."
)


@dataclass(frozen=True)
class PlanStepChange:
    card_id: str
    title: str
    action: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None


@dataclass(frozen=True)
class PlanFileDeliverable:
    path: str
    label: str


@dataclass(frozen=True)
class PlanMessageDeliverable:
    message_id: str
    label: str


@dataclass(frozen=True)
class PlanSnapshot:
    todo_count: int
    doing_count: int
    done_count: int
    todo_titles: Sequence[str]
    doing_titles: Sequence[str]
    done_titles: Sequence[str]
    files: Sequence[PlanFileDeliverable] = ()
    messages: Sequence[PlanMessageDeliverable] = ()


def get_update_plan_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": PLAN_TOOL_NAME,
            "description": (
                "Replace the visible task plan for substantial multi-step work. Keep 3-6 current, verifiable steps with at most one doing; omit stale work and recurrence-by-recurrence entries. "
                "Do not use for quick answers, routine scheduled briefings, or progress narration. Each call replaces all plan and deliverable entries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "array",
                        "description": "The list of steps",
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string", "maxLength": 255},
                                "status": {
                                    "type": "string",
                                    "description": "One of: todo, doing, done",
                                    "enum": ["todo", "doing", "done"],
                                },
                            },
                            "required": ["step", "status"],
                            "additionalProperties": False,
                        },
                    },
                    "files": {
                        "type": "array",
                        "description": (
                            "Complete current list of final user-visible files; omit scratch and intermediate files."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Filespace path for the deliverable, e.g. /exports/report.csv.",
                                },
                                "label": {
                                    "type": "string",
                                    "description": "Short user-facing label, e.g. Final CSV report.",
                                },
                            },
                            "required": ["path"],
                            "additionalProperties": False,
                        },
                    },
                    "messages": {
                        "type": "array",
                        "description": (
                            "Optional final message deliverables already sent to the user and returned by the send tool. "
                            f"{MESSAGE_DELIVERABLE_GUIDANCE} Include the complete current message deliverable list on every update."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "message_id": {
                                    "type": "string",
                                    "description": (
                                        "Exact user-facing send-tool message_id; no placeholders, URLs, or peer messages."
                                    ),
                                },
                                "label": {
                                    "type": "string",
                                    "description": "Short user-facing label, e.g. Final report message.",
                                },
                            },
                            "required": ["message_id"],
                            "additionalProperties": False,
                        },
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = continue after this plan update; false = stop because all work is done or deferred and no current plan items remain unfinished.",
                    },
                },
                "required": ["plan", "will_continue_work"],
                "additionalProperties": False,
            },
        },
    }


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return None


def execute_update_plan(agent, params: dict[str, Any]) -> dict[str, Any]:
    validation = _validate_update_plan_params(agent, params)
    if validation["errors"]:
        return {
            "status": "error",
            "message": _format_plan_validation_message(validation["errors"]),
            "errors": validation["errors"],
        }

    plan_items = validation["plan"]
    file_items = validation["files"]
    message_items = validation["messages"]

    changes: list[PlanStepChange] = []
    with transaction.atomic():
        existing_cards = list(
            PersistentAgentKanbanCard.objects.select_for_update()
            .filter(assigned_agent=agent)
            .order_by("-priority", "created_at")
        )
        existing_by_key: dict[str, list[PersistentAgentKanbanCard]] = {}
        for card in existing_cards:
            existing_by_key.setdefault(_normalize_step_key(card.title), []).append(card)
        matched_existing_card_ids: set[str] = set()
        total = len(plan_items)

        for index, item in enumerate(plan_items):
            step = item["step"]
            status = item["status"]
            key = _normalize_step_key(step)
            priority = total - index
            matching_cards = existing_by_key.get(key)
            card = matching_cards.pop(0) if matching_cards else None

            if card is None:
                completed_at = timezone.now() if status == PersistentAgentKanbanCard.Status.DONE else None
                card = PersistentAgentKanbanCard.objects.create(
                    assigned_agent=agent,
                    title=step,
                    description="",
                    status=status,
                    priority=priority,
                    completed_at=completed_at,
                )
                changes.append(
                    PlanStepChange(
                        card_id=str(card.id),
                        title=card.title,
                        action="created",
                        to_status=card.status,
                    )
                )
                continue

            matched_existing_card_ids.add(str(card.id))
            old_status = card.status
            update_fields: list[str] = []
            non_status_changed = False
            if card.title != step:
                card.title = step
                update_fields.append("title")
                non_status_changed = True
            if card.description:
                card.description = ""
                update_fields.append("description")
                non_status_changed = True
            if card.priority != priority:
                card.priority = priority
                update_fields.append("priority")
                non_status_changed = True
            if card.status != status:
                card.status = status
                update_fields.append("status")
                if status == PersistentAgentKanbanCard.Status.DONE:
                    if card.completed_at is None:
                        card.completed_at = timezone.now()
                        update_fields.append("completed_at")
                elif card.completed_at is not None:
                    card.completed_at = None
                    update_fields.append("completed_at")

            if update_fields:
                update_fields.append("updated_at")
                card.save(update_fields=list(dict.fromkeys(update_fields)))
                if old_status != status:
                    changes.append(
                        PlanStepChange(
                            card_id=str(card.id),
                            title=card.title,
                            action=_action_for_status_change(status),
                            from_status=old_status,
                            to_status=status,
                        )
                    )
                elif non_status_changed:
                    changes.append(
                        PlanStepChange(
                            card_id=str(card.id),
                            title=card.title,
                            action="updated",
                            from_status=old_status,
                            to_status=status,
                        )
                    )

        for card in existing_cards:
            if str(card.id) in matched_existing_card_ids:
                continue
            changes.append(
                PlanStepChange(
                    card_id=str(card.id),
                    title=card.title,
                    action="deleted",
                    from_status=card.status,
                )
            )
            card.delete()

        PersistentAgentPlanDeliverable.objects.filter(agent=agent).delete()
        deliverables: list[PersistentAgentPlanDeliverable] = []
        position = 0
        for item in file_items:
            deliverables.append(
                PersistentAgentPlanDeliverable(
                    agent=agent,
                    kind=PersistentAgentPlanDeliverable.Kind.FILE,
                    label=item["label"],
                    path=item["path"],
                    position=position,
                )
            )
            position += 1
        for item in message_items:
            deliverables.append(
                PersistentAgentPlanDeliverable(
                    agent=agent,
                    kind=PersistentAgentPlanDeliverable.Kind.MESSAGE,
                    label=item["label"],
                    message_id=item["message_id"],
                    position=position,
                )
            )
            position += 1
        if deliverables:
            PersistentAgentPlanDeliverable.objects.bulk_create(deliverables)

    snapshot = build_plan_snapshot(agent)
    _broadcast_plan_changes(agent, changes, snapshot)
    result = {
        "status": "ok",
        "message": "Plan updated.",
        "step_count": len(plan_items),
        "todo_count": snapshot.todo_count,
        "doing_count": snapshot.doing_count,
        "done_count": snapshot.done_count,
    }
    will_continue_work = _coerce_optional_bool(params.get("will_continue_work"))
    if will_continue_work is not None:
        result["auto_sleep_ok"] = not will_continue_work
    return result


def build_redundant_research_plan_skip_result(agent, params: dict[str, Any]) -> dict[str, Any] | None:
    raw_plan = params.get("plan")
    if not isinstance(raw_plan, list) or not raw_plan:
        return None
    if params.get("files") or params.get("messages"):
        return None

    existing_cards = list(PersistentAgentKanbanCard.objects.filter(assigned_agent=agent))
    if not existing_cards:
        return None

    incoming_steps: list[str] = []
    incoming_statuses: list[str] = []
    for item in raw_plan:
        if not isinstance(item, dict):
            return None
        step = str(item.get("step") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        if not step or status not in PLAN_STATUSES:
            return None
        incoming_steps.append(step)
        incoming_statuses.append(status)

    if len(incoming_steps) != len(existing_cards):
        return None

    existing_statuses_by_key = {
        _normalize_step_key(card.title): card.status
        for card in existing_cards
    }
    incoming_statuses_by_key = {
        _normalize_step_key(step): status
        for step, status in zip(incoming_steps, incoming_statuses)
    }
    if (
        set(existing_statuses_by_key) != set(incoming_statuses_by_key)
        or any(incoming_statuses_by_key[key] != existing_statuses_by_key[key] for key in incoming_statuses_by_key)
    ):
        return None

    plan_text = " ".join(incoming_steps).casefold()
    existing_plan_text = " ".join(card.title for card in existing_cards).casefold()
    matched_terms = sum(1 for term in _RESEARCH_PLAN_TERMS if term in plan_text)
    existing_matched_terms = sum(1 for term in _RESEARCH_PLAN_TERMS if term in existing_plan_text)
    if matched_terms < 2 or existing_matched_terms < 2:
        return None

    will_continue_work = _coerce_optional_bool(params.get("will_continue_work"))
    if will_continue_work is False:
        return {
            "status": "ok",
            "message": (
                "Skipped redundant research plan completion update. The final answer was already delivered or "
                "the task requested stopping."
            ),
            "skipped": True,
            "auto_sleep_ok": True,
        }

    return {
        "status": "ok",
        "message": (
            "Skipped redundant research plan status update. Continue with the remaining research, then send the "
            "final answer directly."
        ),
        "skipped": True,
        "auto_sleep_ok": False,
    }


def build_plan_snapshot(agent) -> PlanSnapshot:
    cards = list(
        PersistentAgentKanbanCard.objects.filter(assigned_agent=agent).order_by("-priority", "created_at")
    )
    todo_titles: list[str] = []
    doing_titles: list[str] = []
    done_titles: list[str] = []
    for card in cards:
        if card.status == PersistentAgentKanbanCard.Status.TODO:
            todo_titles.append(card.title)
        elif card.status == PersistentAgentKanbanCard.Status.DOING:
            doing_titles.append(card.title)
        elif card.status == PersistentAgentKanbanCard.Status.DONE:
            done_titles.append(card.title)

    files: list[PlanFileDeliverable] = []
    messages: list[PlanMessageDeliverable] = []
    deliverables = PersistentAgentPlanDeliverable.objects.filter(agent=agent).select_related("message")
    for deliverable in deliverables:
        if deliverable.kind == PersistentAgentPlanDeliverable.Kind.FILE and deliverable.path:
            files.append(
                PlanFileDeliverable(
                    path=deliverable.path,
                    label=deliverable.label or _default_file_label(deliverable.path),
                )
            )
        elif deliverable.kind == PersistentAgentPlanDeliverable.Kind.MESSAGE and deliverable.message_id:
            messages.append(
                PlanMessageDeliverable(
                    message_id=str(deliverable.message_id),
                    label=deliverable.label or "Message",
                )
            )

    return PlanSnapshot(
        todo_count=len(todo_titles),
        doing_count=len(doing_titles),
        done_count=len(done_titles),
        todo_titles=todo_titles,
        doing_titles=doing_titles,
        done_titles=done_titles,
        files=files,
        messages=messages,
    )


def format_current_plan_for_prompt(agent) -> str:
    snapshot = build_plan_snapshot(agent)
    if not (snapshot.todo_count or snapshot.doing_count or snapshot.done_count):
        return "Current plan: none"

    lines = [
        "Current plan:",
        f"- Doing: {snapshot.doing_count}",
        f"- Todo: {snapshot.todo_count}",
        f"- Done: {snapshot.done_count}",
    ]
    for label, titles in (
        ("Doing", snapshot.doing_titles),
        ("Todo", snapshot.todo_titles),
        ("Done", snapshot.done_titles),
    ):
        if titles:
            lines.append(f"{label}:")
            lines.extend(f"- {title}" for title in titles[:20])
    return "\n".join(lines)


def _validate_update_plan_params(agent, params: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    raw_plan = params.get("plan")
    if not isinstance(raw_plan, list):
        errors.append("plan must be a list.")
        raw_plan = []
    elif len(raw_plan) > 6:
        errors.append("plan may contain at most 6 steps.")

    plan_items: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    doing_count = 0
    for index, item in enumerate(raw_plan):
        if not isinstance(item, dict):
            errors.append(f"plan[{index}] must be an object.")
            continue
        step = str(item.get("step") or "").strip()
        if not step:
            errors.append(f"plan[{index}].step is required.")
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in PLAN_STATUSES:
            errors.append(f"plan[{index}].status must be one of: todo, doing, done.")
            continue
        key = _normalize_step_key(step)
        if key in seen_keys:
            errors.append(f"Duplicate plan step: {step}")
            continue
        seen_keys.add(key)
        if status == PersistentAgentKanbanCard.Status.DOING:
            doing_count += 1
        plan_items.append({"step": step[:255], "status": status})
    if doing_count > 1:
        errors.append("At most one plan step may be doing.")

    file_items = _validate_file_deliverables(params.get("files"), errors)
    message_items = _validate_message_deliverables(agent, params.get("messages"), errors)
    return {"errors": errors, "plan": plan_items, "files": file_items, "messages": message_items}


def _format_plan_validation_message(errors: list[str]) -> str:
    if not errors:
        return "Plan update rejected."
    first_error = errors[0]
    message = f"Plan update rejected: {first_error}"
    if any(error.startswith("messages") for error in errors):
        message = f"{message} {MESSAGE_DELIVERABLE_GUIDANCE}"
    return message


def _validate_file_deliverables(raw_files: Any, errors: list[str]) -> list[dict[str, str]]:
    if raw_files is None:
        return []
    if not isinstance(raw_files, list):
        errors.append("files must be a list.")
        return []
    file_items: list[dict[str, str]] = []
    for index, item in enumerate(raw_files):
        if not isinstance(item, dict):
            errors.append(f"files[{index}] must be an object.")
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            errors.append(f"files[{index}].path is required.")
            continue
        if not path.startswith("/"):
            errors.append(f"files[{index}].path must be a filespace path starting with '/'.")
            continue
        label = str(item.get("label") or "").strip() or _default_file_label(path)
        file_items.append({"path": path[:1024], "label": label[:255]})
    return file_items


def _validate_message_deliverables(agent, raw_messages: Any, errors: list[str]) -> list[dict[str, str]]:
    if raw_messages is None:
        return []
    if not isinstance(raw_messages, list):
        errors.append("messages must be a list.")
        return []

    parsed_message_ids: dict[int, str] = {}
    valid_message_ids: set[str] = set()
    for index, item in enumerate(raw_messages):
        if not isinstance(item, dict):
            continue
        raw_message_id = str(item.get("message_id") or "").strip()
        if not raw_message_id:
            continue
        try:
            message_id = str(UUID(raw_message_id))
        except (TypeError, ValueError, AttributeError):
            errors.append(f"messages[{index}].message_id must be a valid UUID.")
            continue
        parsed_message_ids[index] = message_id
        valid_message_ids.add(message_id)

    existing_message_ids = set()
    if valid_message_ids:
        existing_message_ids = {
            str(message_id)
            for message_id in PersistentAgentMessage.objects.filter(
                id__in=valid_message_ids,
                owner_agent=agent,
            ).values_list("id", flat=True)
        }

    message_items: list[dict[str, str]] = []
    for index, item in enumerate(raw_messages):
        if not isinstance(item, dict):
            errors.append(f"messages[{index}] must be an object.")
            continue
        raw_message_id = str(item.get("message_id") or "").strip()
        if not raw_message_id:
            errors.append(f"messages[{index}].message_id is required.")
            continue
        message_id = parsed_message_ids.get(index)
        if message_id is None:
            continue
        if message_id not in existing_message_ids:
            errors.append(f"messages[{index}].message_id does not reference a message for this agent.")
            continue
        label = str(item.get("label") or "").strip() or "Message"
        message_items.append({"message_id": message_id, "label": label[:255]})
    return message_items


def _normalize_step_key(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip().lower())


def _action_for_status_change(status: str) -> str:
    if status == PersistentAgentKanbanCard.Status.DONE:
        return "completed"
    if status == PersistentAgentKanbanCard.Status.DOING:
        return "started"
    return "updated"


def _default_file_label(path: str) -> str:
    name = os.path.basename((path or "").rstrip("/"))
    return name or path or "File"


def _broadcast_plan_changes(agent, changes: Sequence[PlanStepChange], snapshot: PlanSnapshot) -> None:
    try:
        from console.agent_chat.signals import broadcast_plan_changes

        broadcast_plan_changes(agent, changes, snapshot)
    except (ImportError, RuntimeError):
        logger.warning("Failed to import plan broadcast helper for agent %s", getattr(agent, "id", None), exc_info=True)
