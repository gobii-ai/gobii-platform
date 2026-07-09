from typing import Any, Iterable

from api.models import PersistentAgent, PersistentAgentHumanInputRequest, PersistentAgentUserActionEvent


def _clean_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    return {str(key): value for key, value in metadata.items() if value is not None}


def _human_input_response_metadata(
    request_obj: PersistentAgentHumanInputRequest,
) -> dict[str, Any]:
    answer = request_obj.selected_option_title or request_obj.free_text or request_obj.raw_reply_text
    return {
        "request_id": str(request_obj.id),
        "question": request_obj.question,
        "answer": answer,
        "answer_type": "selected_option" if request_obj.selected_option_key else "free_text",
        "selected_option_key": request_obj.selected_option_key or None,
    }


def create_user_action_event(
    *,
    agent: PersistentAgent,
    actor_user,
    action_type: str,
    count: int = 1,
    metadata: dict[str, Any] | None = None,
) -> PersistentAgentUserActionEvent:
    return PersistentAgentUserActionEvent.objects.create(
        agent=agent,
        actor_user=actor_user if getattr(actor_user, "is_authenticated", False) else None,
        action_type=action_type,
        count=max(int(count or 1), 1),
        metadata=_clean_metadata(metadata),
    )


def _label_list(labels: Iterable[str]) -> list[str]:
    return [label for label in labels if label]


def record_human_input_answered(
    *,
    agent: PersistentAgent,
    actor_user,
    request_ids: Iterable[str],
    responses: Iterable[PersistentAgentHumanInputRequest] | None = None,
) -> PersistentAgentUserActionEvent:
    request_id_list = list(request_ids)
    metadata: dict[str, Any] = {"request_ids": request_id_list}
    if responses is not None:
        metadata["responses"] = [_human_input_response_metadata(request_obj) for request_obj in responses]
    return create_user_action_event(
        agent=agent,
        actor_user=actor_user,
        action_type=PersistentAgentUserActionEvent.ActionType.HUMAN_INPUT_ANSWERED,
        count=max(len(request_id_list), 1),
        metadata=metadata,
    )


def record_human_input_dismissed(
    *,
    agent: PersistentAgent,
    actor_user,
    request_id: str,
) -> PersistentAgentUserActionEvent:
    return create_user_action_event(
        agent=agent,
        actor_user=actor_user,
        action_type=PersistentAgentUserActionEvent.ActionType.HUMAN_INPUT_DISMISSED,
        metadata={"request_ids": [str(request_id)]},
    )


def _record_requested_secrets_action(
    agent: PersistentAgent,
    actor_user,
    action_type: str,
    secret_labels: Iterable[str],
    **metadata,
) -> PersistentAgentUserActionEvent:
    labels = _label_list(secret_labels)
    return create_user_action_event(
        agent=agent,
        actor_user=actor_user,
        action_type=action_type,
        count=max(len(labels), 1),
        metadata={"secret_names": labels, **metadata},
    )


def record_requested_secrets_saved(
    *,
    agent: PersistentAgent,
    actor_user,
    secret_labels: Iterable[str],
    make_global: bool,
) -> PersistentAgentUserActionEvent:
    return _record_requested_secrets_action(
        agent,
        actor_user,
        PersistentAgentUserActionEvent.ActionType.SECRETS_SAVED,
        secret_labels,
        scope="global" if make_global else "agent",
    )


def record_requested_secrets_removed(
    *,
    agent: PersistentAgent,
    actor_user,
    secret_labels: Iterable[str],
) -> PersistentAgentUserActionEvent:
    return _record_requested_secrets_action(
        agent,
        actor_user,
        PersistentAgentUserActionEvent.ActionType.SECRETS_REMOVED,
        secret_labels,
    )


def record_contact_requests_resolved(
    *,
    agent: PersistentAgent,
    actor_user,
    approved_count: int,
    declined_count: int,
    skipped_count: int,
    contact_labels: Iterable[str],
) -> PersistentAgentUserActionEvent | None:
    total = approved_count + declined_count
    if total <= 0:
        return None

    if approved_count and declined_count:
        action_type = PersistentAgentUserActionEvent.ActionType.CONTACTS_RESOLVED
    elif approved_count:
        action_type = PersistentAgentUserActionEvent.ActionType.CONTACTS_APPROVED
    else:
        action_type = PersistentAgentUserActionEvent.ActionType.CONTACTS_DECLINED

    return create_user_action_event(
        agent=agent,
        actor_user=actor_user,
        action_type=action_type,
        count=total,
        metadata={
            "approved_count": approved_count,
            "declined_count": declined_count,
            "skipped_count": skipped_count,
            "contact_labels": _label_list(contact_labels),
        },
    )
