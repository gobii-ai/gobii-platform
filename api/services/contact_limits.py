import logging
from dataclasses import dataclass
from typing import Any

from django.db import DatabaseError, transaction
from django.db.models import Count
from django.utils import timezone

from billing.services import BillingService
from util.subscription_helper import get_user_max_contacts_per_agent

from api.models import CommsChannel, CommsOutboundContactUsage, PersistentAgent
from email.utils import parseaddr

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContactCapResult:
    allowed: bool
    reason: str | None = None
    remaining: int | None = None
    limit: int | None = None


def normalize_contact_address(channel_val: str, address: str | None) -> str:
    raw = (address or "").strip()
    if channel_val == CommsChannel.EMAIL:
        return (parseaddr(raw)[1] or raw).strip().lower()
    return raw


def get_contact_cap(agent: PersistentAgent) -> int:
    return get_user_max_contacts_per_agent(
        agent.user,
        organization=agent.organization,
    )


def get_billing_period(agent: PersistentAgent) -> tuple[Any, Any]:
    owner = agent.organization or agent.user
    try:
        return BillingService.get_current_billing_period_for_owner(owner)
    except (AttributeError, ValueError, TypeError, DatabaseError):
        logger.exception("Failed to resolve billing period for agent %s", agent.id)
        today = timezone.localdate()
        return today, today


def check_and_register_outbound_contacts(
    agent: PersistentAgent,
    channel: CommsChannel | str,
    addresses: list[str],
) -> ContactCapResult:
    channel_val = channel.value if isinstance(channel, CommsChannel) else str(channel)
    if not addresses:
        return ContactCapResult(False, "Recipient address is required.")

    normalized_set: set[str] = set()
    for address in addresses:
        normalized = normalize_contact_address(channel_val, address)
        if not normalized:
            return ContactCapResult(False, "Recipient address is required.")
        if agent.is_privileged_contact(channel_val, normalized):
            continue
        normalized_set.add(normalized)

    if not normalized_set:
        return ContactCapResult(True)

    cap = get_contact_cap(agent)
    if cap <= 0:
        return ContactCapResult(True)

    period_start, period_end = get_billing_period(agent)
    now = timezone.now()

    try:
        with transaction.atomic():
            usage_qs = (
                CommsOutboundContactUsage.objects
                .select_for_update()
                .filter(
                    agent=agent,
                    channel=channel_val,
                    period_start=period_start,
                )
            )
            existing_addresses = set(
                usage_qs.filter(address__in=normalized_set).values_list("address", flat=True)
            )
            new_addresses = [addr for addr in normalized_set if addr not in existing_addresses]
            if existing_addresses:
                usage_qs.filter(address__in=existing_addresses).update(last_used_at=now)

            used_count = usage_qs.count()
            if used_count + len(new_addresses) > cap:
                return ContactCapResult(
                    False,
                    "Contact limit reached for this channel. Purchase contact packs or wait for the next billing cycle.",
                    remaining=0,
                    limit=cap,
                )

            for address in new_addresses:
                CommsOutboundContactUsage.objects.create(
                    agent=agent,
                    channel=channel_val,
                    address=address,
                    period_start=period_start,
                    period_end=period_end,
                )
            remaining = max(0, cap - (used_count + len(new_addresses)))
            return ContactCapResult(True, remaining=remaining, limit=cap)
    except DatabaseError:
        logger.exception("Failed to record outbound contact usage for agent %s", agent.id)
        return ContactCapResult(True)


def check_and_register_outbound_contact(
    agent: PersistentAgent,
    channel: CommsChannel | str,
    address: str,
) -> ContactCapResult:
    return check_and_register_outbound_contacts(agent, channel, [address])


def get_contact_usage_summary(agent: PersistentAgent) -> dict[str, Any]:
    cap = get_contact_cap(agent)
    limit_value = None if cap <= 0 else cap
    period_start, period_end = get_billing_period(agent)
    channel_counts: dict[str, int] = {}

    try:
        rows = (
            CommsOutboundContactUsage.objects
            .filter(agent=agent, period_start=period_start)
            .values("channel")
            .annotate(total=Count("id"))
        )
        channel_counts = {row["channel"]: int(row["total"] or 0) for row in rows}
    except DatabaseError:
        logger.exception("Failed to compute contact usage for agent %s", agent.id)

    channels = []
    for channel_val in (CommsChannel.EMAIL, CommsChannel.SMS, CommsChannel.WEB):
        used = channel_counts.get(channel_val, 0)
        remaining = None if limit_value is None else max(0, limit_value - used)
        channels.append(
            {
                "channel": channel_val,
                "used": used,
                "limit": limit_value,
                "remaining": remaining,
            }
        )

    total_used = sum(channel_counts.values())
    return {
        "limit_per_channel": limit_value,
        "channels": channels,
        "total_used": total_used,
        "period_start": period_start.isoformat() if hasattr(period_start, "isoformat") else period_start,
        "period_end": period_end.isoformat() if hasattr(period_end, "isoformat") else period_end,
    }
