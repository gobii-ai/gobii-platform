import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from api.models import DecodoLowInventoryAlert, ProxyServer

logger = logging.getLogger(__name__)


def get_active_decodo_proxy_counts() -> tuple[int, int, int]:
    total_count = ProxyServer.objects.filter(
        is_active=True,
        decodo_ip__isnull=False,
    ).count()
    dedicated_count = ProxyServer.objects.filter(
        is_active=True,
        decodo_ip__isnull=False,
        dedicated_allocation__isnull=False,
    ).count()
    shared_count = total_count - dedicated_count
    return total_count, shared_count, dedicated_count


def maybe_send_decodo_low_inventory_alert(*, reason: str | None = None) -> bool:
    threshold = settings.DECODO_LOW_INVENTORY_THRESHOLD
    recipient = (getattr(settings, "DECODO_LOW_INVENTORY_EMAIL", "") or "").strip()
    if not recipient:
        logger.warning("Decodo low inventory alert skipped: no recipient configured.")
        return False

    total_count, shared_count, dedicated_count = get_active_decodo_proxy_counts()
    if shared_count >= threshold:
        return False

    sent_on = timezone.localdate()
    alert, created = DecodoLowInventoryAlert.objects.get_or_create(
        sent_on=sent_on,
        defaults={
            "active_proxy_count": shared_count,
            "threshold": threshold,
            "recipient_email": recipient,
        },
    )
    if not created:
        return False

    subject = f"Decodo proxy inventory low ({shared_count} shared active)"
    body_lines = [
        "Decodo proxy inventory is below the configured threshold.",
        "",
        f"Active shared proxies: {shared_count}",
        f"Active dedicated allocations: {dedicated_count}",
        f"Active Decodo proxies (total): {total_count}",
        f"Threshold: {threshold} shared proxies",
    ]
    if reason:
        body_lines.append(f"Trigger: {reason}")
    body_lines.extend(["", "Please provision additional Decodo IPs."])
    body = "\n".join(body_lines)

    sent_count = send_mail(
        subject=subject,
        message=body,
        from_email=None,
        recipient_list=[recipient],
        fail_silently=True,
    )
    if sent_count:
        return True

    alert.delete()
    return False
