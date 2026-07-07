from collections.abc import Iterable
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from api.models import ProductAnnouncement, ProductAnnouncementRead

RECENT_PRODUCT_ANNOUNCEMENT_LIMIT = 5


def visible_product_announcements_queryset(now=None):
    current_time = now or timezone.now()
    return (
        ProductAnnouncement.objects
        .filter(is_active=True, published_at__lte=current_time)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=current_time))
        .order_by("-published_at", "-created_at", "-id")
    )


def _isoformat_or_none(value) -> str | None:
    return value.isoformat() if value else None


def _serialize_announcement(
    announcement: ProductAnnouncement,
    read_at_by_id: dict[str, object],
) -> dict[str, object]:
    read_at = read_at_by_id.get(str(announcement.id))
    return {
        "id": str(announcement.id),
        "title": announcement.title,
        "body": announcement.body,
        "actionLabel": announcement.action_label or None,
        "actionUrl": announcement.action_url or None,
        "publishedAt": _isoformat_or_none(announcement.published_at),
        "readAt": _isoformat_or_none(read_at),
        "isRead": bool(read_at),
    }


def build_product_announcements_payload(
    user,
    *,
    recent_limit: int = RECENT_PRODUCT_ANNOUNCEMENT_LIMIT,
) -> dict[str, object]:
    visible_qs = visible_product_announcements_queryset()
    recent_announcements = list(visible_qs[:recent_limit])
    recent_ids = [announcement.id for announcement in recent_announcements]
    read_at_by_id: dict[str, object] = {}
    if recent_ids:
        read_at_by_id = {
            str(row["announcement_id"]): row["read_at"]
            for row in (
                ProductAnnouncementRead.objects
                .filter(user=user, announcement_id__in=recent_ids)
                .values("announcement_id", "read_at")
            )
        }

    unread_count = visible_qs.exclude(read_receipts__user=user).count()
    return {
        "announcements": [
            _serialize_announcement(announcement, read_at_by_id)
            for announcement in recent_announcements
        ],
        "unreadCount": unread_count,
        "hasUnread": unread_count > 0,
        "recentLimit": recent_limit,
    }


def mark_product_announcements_read(
    user,
    *,
    announcement_ids: Iterable[UUID] | None = None,
    mark_all: bool = False,
) -> dict[str, object]:
    visible_qs = visible_product_announcements_queryset()
    if mark_all:
        target_ids = list(visible_qs.values_list("id", flat=True))
    else:
        target_ids = list(announcement_ids or [])
        if target_ids:
            target_ids = list(
                visible_qs
                .filter(id__in=target_ids)
                .values_list("id", flat=True)
            )

    if target_ids:
        read_at = timezone.now()
        ProductAnnouncementRead.objects.bulk_create(
            [
                ProductAnnouncementRead(
                    announcement_id=announcement_id,
                    user=user,
                    read_at=read_at,
                )
                for announcement_id in target_ids
            ],
            ignore_conflicts=True,
        )

    return build_product_announcements_payload(user)
