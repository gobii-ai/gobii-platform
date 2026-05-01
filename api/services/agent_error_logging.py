import logging
import traceback as traceback_module
from collections.abc import Mapping, Sequence
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import DataError, DatabaseError, IntegrityError

from api.models import PersistentAgent, PersistentAgentCompletion, PersistentAgentError


MAX_MESSAGE_LENGTH = 4000
MAX_TRACEBACK_LENGTH = 20000
MAX_CONTEXT_VALUE_LENGTH = 2000
MAX_CONTEXT_DEPTH = 4
MAX_CONTEXT_ITEMS = 50


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 14]}...[truncated]"


def _safe_context_value(value, *, depth: int = 0):
    if depth >= MAX_CONTEXT_DEPTH:
        return _truncate(str(value), MAX_CONTEXT_VALUE_LENGTH)

    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str):
            return _truncate(value, MAX_CONTEXT_VALUE_LENGTH)
        return value

    if isinstance(value, (Decimal, UUID)):
        return str(value)

    if isinstance(value, Mapping):
        safe = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_CONTEXT_ITEMS:
                safe["__truncated__"] = True
                break
            safe[_truncate(str(key), 128)] = _safe_context_value(item, depth=depth + 1)
        return safe

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        safe_items = []
        for index, item in enumerate(value):
            if index >= MAX_CONTEXT_ITEMS:
                safe_items.append("[truncated]")
                break
            safe_items.append(_safe_context_value(item, depth=depth + 1))
        return safe_items

    return _truncate(str(value), MAX_CONTEXT_VALUE_LENGTH)


def _safe_context(context: dict | None) -> dict:
    if not isinstance(context, dict):
        return {}
    safe = _safe_context_value(context)
    return safe if isinstance(safe, dict) else {}


def _traceback_for_exception(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    return _truncate(
        "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__)),
        MAX_TRACEBACK_LENGTH,
    )


def log_agent_error(
    agent: PersistentAgent,
    *,
    category: str,
    source: str,
    message: str,
    exc: BaseException | None = None,
    logger: logging.Logger | None = None,
    level: int = logging.ERROR,
    completion: PersistentAgentCompletion | None = None,
    context: dict | None = None,
    log_exc_info: bool = True,
) -> PersistentAgentError | None:
    target_logger = logger or logging.getLogger(__name__)
    message_text = _truncate(str(message or ""), MAX_MESSAGE_LENGTH)
    safe_context = _safe_context(context)
    log_extra = {"agent_error_context": safe_context} if safe_context else None

    if exc is not None and log_exc_info:
        target_logger.log(
            level,
            message_text,
            exc_info=(type(exc), exc, exc.__traceback__),
            extra=log_extra,
        )
    else:
        target_logger.log(level, message_text, extra=log_extra)

    try:
        return PersistentAgentError.objects.create(
            agent=agent,
            completion=completion,
            category=category,
            source=_truncate(str(source or ""), 256),
            level=logging.getLevelName(level) if isinstance(logging.getLevelName(level), str) else str(level),
            message=message_text,
            exception_class=_truncate(type(exc).__name__, 256) if exc is not None else "",
            traceback=_traceback_for_exception(exc),
            context=safe_context,
        )
    except (DataError, DatabaseError, IntegrityError, TypeError, ValueError):
        target_logger.warning(
            "Failed to persist agent error event for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )
        return None


def validation_error_messages(exc: ValidationError) -> list[str]:
    messages: list[str] = []
    try:
        if isinstance(getattr(exc, "message_dict", None), dict):
            for value in exc.message_dict.values():
                if isinstance(value, (list, tuple)):
                    messages.extend(str(item) for item in value)
                else:
                    messages.append(str(value))
    except (AttributeError, TypeError, ValueError):
        pass

    try:
        messages.extend(str(message) for message in getattr(exc, "messages", []))
    except (AttributeError, TypeError, ValueError):
        pass
    return messages


def log_task_quota_exceeded(
    persistent_agent_id: str,
    exc: ValidationError,
    *,
    source: str,
    logger: logging.Logger | None = None,
    task_id: str | None = None,
) -> PersistentAgentError | None:
    target_logger = logger or logging.getLogger(__name__)
    try:
        agent = PersistentAgent.objects.filter(id=persistent_agent_id).first()
    except (DatabaseError, ValidationError, ValueError, TypeError):
        target_logger.warning(
            "Failed to resolve agent %s while logging task quota error",
            persistent_agent_id,
            exc_info=True,
        )
        return None

    if agent is None:
        target_logger.warning("Cannot persist quota error for missing agent %s", persistent_agent_id)
        return None

    return log_agent_error(
        agent,
        category=PersistentAgentError.Category.TASK_QUOTA_EXCEEDED,
        source=source,
        message=f"Task quota exceeded for agent {persistent_agent_id}",
        exc=exc,
        logger=target_logger,
        level=logging.INFO,
        context={
            "agent_id": str(persistent_agent_id),
            "task_id": task_id,
            "validation_messages": validation_error_messages(exc),
        },
        log_exc_info=False,
    )
