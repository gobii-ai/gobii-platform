"""Plan update tool for persistent agents."""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from django.db import transaction
from django.utils import timezone

from api.models import (
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
    PersistentAgentPlanDeliverable,
)

logger = logging.getLogger(__name__)

PLAN_TOOL_NAME = "update_plan"
PLAN_STATUSES = {
    PersistentAgentKanbanCard.Status.TODO,
    PersistentAgentKanbanCard.Status.DOING,
    PersistentAgentKanbanCard.Status.DONE,
}
_WHITESPACE_RE = re.compile(r"\s+")


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
                "Updates the task plan.\n"
                "Provide a list of plan items, each with a step and status.\n"
                "At most one step can be doing at a time.\n"
                "Every call replaces the full current plan, including the deliverable references."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "array",
                        "description": "The list of steps",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
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
                            "Optional final file deliverables created during the work. Use this for user-visible artifacts "
                            "such as reports, CSV exports, PDFs, charts, or generated documents that should remain attached "
                            "to the completed plan. Include the complete current file deliverable list on every update; omit "
                            "scratch files, temporary downloads, and intermediate analysis files."
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
                            "Optional final message deliverables associated with the work. Use this after sending a final "
                            "report, answer, or important user-facing summary so the completed plan links to that delivered "
                            "message. Include the complete current message deliverable list on every update; do not add routine "
                            "progress updates, greetings, or internal/status messages."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "message_id": {
                                    "type": "string",
                                    "description": "ID returned by the send tool for the delivered user-facing message.",
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
                        "description": "REQUIRED. true = continue after this plan update; false = stop because all work is done or deferred.",
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
            "message": "Plan update rejected.",
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


def _validate_update_plan_params(agent, params: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    raw_plan = params.get("plan")
    if not isinstance(raw_plan, list):
        errors.append("plan must be a list.")
        raw_plan = []

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
    message_items: list[dict[str, str]] = []
    for index, item in enumerate(raw_messages):
        if not isinstance(item, dict):
            errors.append(f"messages[{index}] must be an object.")
            continue
        raw_message_id = str(item.get("message_id") or "").strip()
        if not raw_message_id:
            errors.append(f"messages[{index}].message_id is required.")
            continue
        message = PersistentAgentMessage.objects.filter(
            id=raw_message_id,
            owner_agent=agent,
        ).only("id").first()
        if not message:
            errors.append(f"messages[{index}].message_id does not reference a message for this agent.")
            continue
        label = str(item.get("label") or "").strip() or "Message"
        message_items.append({"message_id": str(message.id), "label": label[:255]})
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
