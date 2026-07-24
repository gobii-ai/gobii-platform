import base64
import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchVector
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Exists, OuterRef, Q, QuerySet

from api.models import PersistentAgent, PersistentAgentMessage, PersistentAgentMessageAttachment
from console.context_helpers import resolve_console_context, resolve_staff_console_context
from console.context_overrides import get_context_override, get_staff_context_override
from util.text_sanitizer import sanitize_notification_preview_text

from .access import agent_querysets_for_context
from .timeline import visible_message_queryset


DEFAULT_SEARCH_LIMIT = 30
MAX_SEARCH_LIMIT = 50
MAX_SEARCH_QUERY_LENGTH = 256
ATTACHMENT_FILTERS = frozenset({"any", "attachment", "image", "file"})
EXCERPT_LENGTH = 280


class MessageSearchValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MessageSearchCursor:
    timestamp: datetime
    seq: str

    def encode(self) -> str:
        payload = json.dumps(
            {"timestamp": self.timestamp.isoformat(), "seq": self.seq},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, raw: str | None) -> "MessageSearchCursor | None":
        if not raw:
            return None
        try:
            padded = raw + ("=" * (-len(raw) % 4))
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            timestamp = datetime.fromisoformat(payload["timestamp"])
            seq = payload["seq"]
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise MessageSearchValidationError("Invalid cursor.") from exc
        if not isinstance(seq, str) or not seq:
            raise MessageSearchValidationError("Invalid cursor.")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt_timezone.utc)
        return cls(timestamp=timestamp, seq=seq)


def _visible_agent_queryset(request) -> QuerySet:
    staff_override = get_staff_context_override(request)
    if staff_override:
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("Not permitted.")
        context = resolve_staff_console_context(request.user, staff_override).current_context
    else:
        context = resolve_console_context(
            request.user,
            request.session,
            override=get_context_override(request),
        ).current_context
    primary, shared = agent_querysets_for_context(
        request.user,
        context,
        staff_context=bool(staff_override),
        allow_delinquent_personal_chat=True,
    )
    return primary | shared


def _sqlite_query_tokens(query: str) -> tuple[list[list[str]], list[str]]:
    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()

    groups: list[list[str]] = [[]]
    excluded: list[str] = []
    for token in tokens:
        normalized = token.strip()
        if not normalized:
            continue
        if normalized.upper() == "OR":
            if groups[-1]:
                groups.append([])
            continue
        if normalized.startswith("-") and len(normalized) > 1:
            excluded.append(normalized[1:])
            continue
        groups[-1].append(normalized)
    return [group for group in groups if group], excluded


def _apply_sqlite_text_search(
    queryset: QuerySet[PersistentAgentMessage],
    query: str,
) -> QuerySet[PersistentAgentMessage]:
    groups, excluded = _sqlite_query_tokens(query)
    if groups:
        combined = Q()
        for group in groups:
            group_query = Q()
            for term in group:
                group_query &= Q(body__icontains=term)
            combined |= group_query
        queryset = queryset.filter(combined)
    for term in excluded:
        queryset = queryset.exclude(body__icontains=term)
    return queryset


def _positive_highlight_terms(query: str) -> list[str]:
    groups, _excluded = _sqlite_query_tokens(query)
    terms: list[str] = []
    seen: set[str] = set()
    for term in (term for group in groups for term in group):
        normalized = term.casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            terms.append(term)
    return sorted(terms, key=len, reverse=True)


def _excerpt_bounds(text: str, terms: list[str]) -> tuple[int, int]:
    match_start = None
    folded = text.casefold()
    for term in terms:
        index = folded.find(term.casefold())
        if index >= 0 and (match_start is None or index < match_start):
            match_start = index

    if len(text) <= EXCERPT_LENGTH:
        return 0, len(text)

    center = match_start if match_start is not None else 0
    start = max(0, center - EXCERPT_LENGTH // 3)
    end = min(len(text), start + EXCERPT_LENGTH)
    if end - start < EXCERPT_LENGTH:
        start = max(0, end - EXCERPT_LENGTH)
    return start, end


def _highlight_excerpt(excerpt: str, terms: list[str]) -> list[dict[str, Any]]:
    if not terms:
        return [{"text": excerpt, "highlighted": False}]

    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    segments: list[dict[str, Any]] = []
    position = 0
    for match in pattern.finditer(excerpt):
        if match.start() > position:
            segments.append({"text": excerpt[position:match.start()], "highlighted": False})
        segments.append({"text": match.group(0), "highlighted": True})
        position = match.end()
    if position < len(excerpt):
        segments.append({"text": excerpt[position:], "highlighted": False})
    return segments


def _excerpt_segments(body: str, query: str) -> list[dict[str, Any]]:
    text = body or ""
    if not text:
        return [{"text": "", "highlighted": False}]
    terms = _positive_highlight_terms(query)
    start, end = _excerpt_bounds(text, terms)
    excerpt = f"{'…' if start else ''}{text[start:end]}{'…' if end < len(text) else ''}"
    return _highlight_excerpt(excerpt, terms)


def search_agent_messages(
    request,
    *,
    query: str,
    agent_id: str | None,
    attachment_filter: str,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    query = query.strip()
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise MessageSearchValidationError(
            f"q must be at most {MAX_SEARCH_QUERY_LENGTH} characters.",
        )
    if attachment_filter not in ATTACHMENT_FILTERS:
        raise MessageSearchValidationError("Invalid attachment filter.")
    if not query and attachment_filter == "any" and not agent_id:
        raise MessageSearchValidationError(
            "Enter a query or select an agent or attachment filter.",
        )
    if limit < 1 or limit > MAX_SEARCH_LIMIT:
        raise MessageSearchValidationError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}.")

    decoded_cursor = MessageSearchCursor.decode(cursor)
    visible_agents = _visible_agent_queryset(request)
    if agent_id:
        try:
            visible_agents.get(id=agent_id)
        except (PersistentAgent.DoesNotExist, ValueError) as exc:
            raise PermissionDenied("Agent not found.") from exc

    queryset = visible_message_queryset().filter(
        owner_agent_id__in=visible_agents.values("id"),
    )
    if agent_id:
        queryset = queryset.filter(owner_agent_id=agent_id)
    if query:
        if connection.vendor == "postgresql":
            search_document = SearchVector("body", config="simple")
            search_query = SearchQuery(query, config="simple", search_type="websearch")
            queryset = queryset.annotate(search_document=search_document).filter(
                search_document=search_query,
            )
        else:
            queryset = _apply_sqlite_text_search(queryset, query)

    attachments = PersistentAgentMessageAttachment.objects.filter(message_id=OuterRef("pk"))
    attachment_querysets = {
        "attachment": attachments,
        "image": attachments.filter(content_type__istartswith="image/"),
        "file": attachments.exclude(content_type__istartswith="image/"),
    }
    if attachment_filter != "any":
        queryset = queryset.filter(Exists(attachment_querysets[attachment_filter]))

    if decoded_cursor:
        queryset = queryset.filter(
            Q(timestamp__lt=decoded_cursor.timestamp)
            | Q(timestamp=decoded_cursor.timestamp, seq__lt=decoded_cursor.seq),
        )

    messages = list(
        queryset
        .select_related("owner_agent")
        .prefetch_related("attachments")
        .order_by("-timestamp", "-seq")
        [: limit + 1],
    )
    has_more = len(messages) > limit
    page = messages[:limit]
    results = []
    for message in page:
        attachments = list(message.attachments.all())
        normalized_body = sanitize_notification_preview_text(message.body)
        excerpt = _excerpt_segments(normalized_body, query)
        results.append(
            {
                "message_id": str(message.id),
                "timestamp": message.timestamp.isoformat(),
                "excerpt": excerpt,
                "attachment_count": len(attachments),
                "has_images": any(
                    (attachment.content_type or "").lower().startswith("image/")
                    for attachment in attachments
                ),
                "agent": {
                    "id": str(message.owner_agent_id),
                    "name": message.owner_agent.name,
                    "avatar_url": message.owner_agent.get_avatar_thumbnail_url(),
                },
            },
        )

    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = MessageSearchCursor(last.timestamp, last.seq).encode()
    return {"results": results, "next_cursor": next_cursor}
