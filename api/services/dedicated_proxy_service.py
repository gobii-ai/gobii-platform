import logging
from typing import Iterable

from django.conf import settings
from django.db import transaction

from observability import traced

from api.models import DedicatedProxyAllocation, ProxyServer

logger = logging.getLogger(__name__)


class DedicatedProxyUnavailableError(RuntimeError):
    """Raised when no dedicated proxies are available for allocation."""


class DedicatedProxyService:
    """High-level helpers for managing dedicated proxy inventory."""

    @staticmethod
    @traced("DedicatedProxyService allocate_proxy")
    @transaction.atomic
    def allocate_proxy(owner, *, notes: str | None = None) -> ProxyServer:
        """Reserve a dedicated proxy for the supplied owner.

        Selects an available proxy, marks it as allocated, and returns the ProxyServer.
        Raises DedicatedProxyUnavailableError if none are available.
        """
        busy_proxy_ids = DedicatedProxyAllocation.objects.values("proxy_id")

        proxy = (
            ProxyServer.objects.select_for_update(skip_locked=True)
            .filter(
                is_active=True,
                is_dedicated=True,
            )
            .exclude(id__in=busy_proxy_ids)
            .order_by("created_at")
            .first()
        )

        if proxy is None:
            logger.info(
                "No dedicated proxies available for owner %s",
                getattr(owner, "id", None) or owner,
            )
            raise DedicatedProxyUnavailableError("No dedicated proxies available.")

        allocation = DedicatedProxyAllocation.objects.assign_to_owner(proxy, owner, notes=notes)
        logger.info(
            "Dedicated proxy %s allocated to owner %s",
            proxy.id,
            getattr(owner, "id", None) or owner,
        )
        return allocation.proxy

    @staticmethod
    @traced("DedicatedProxyService release_proxy")
    def release_proxy(proxy: ProxyServer) -> bool:
        """Release a proxy back to the dedicated pool.

        Returns True when an allocation existed and was released. Returns False when the
        proxy was already free.
        """
        try:
            allocation = proxy.dedicated_allocation
        except DedicatedProxyAllocation.DoesNotExist:
            return False

        with transaction.atomic():
            allocation.release()

        logger.info("Dedicated proxy %s released back to pool", proxy.id)
        return True

    @staticmethod
    @traced("DedicatedProxyService release_specific")
    @transaction.atomic
    def release_specific(owner, proxy_id: str) -> bool:
        try:
            allocation = DedicatedProxyAllocation.objects.select_for_update().get(proxy_id=proxy_id)
        except DedicatedProxyAllocation.DoesNotExist:
            return False

        if owner is not None:
            expected = allocation.owner_user or allocation.owner_organization
            if expected != owner:
                raise ValueError("Proxy is not owned by the requested owner.")

        allocation.release()
        logger.info("Dedicated proxy %s released back to pool", proxy_id)
        return True

    @staticmethod
    @traced("DedicatedProxyService release_for_owner")
    def release_for_owner(owner, *, limit: int | None = None) -> int:
        """Release some or all proxies held by the specified owner.

        Returns the count of proxies released.
        """
        allocations = DedicatedProxyAllocation.objects.for_owner(owner).select_related("proxy")
        if limit is not None:
            allocations = allocations[:limit]

        released = 0
        with transaction.atomic():
            for allocation in allocations:
                allocation.release()
                released += 1

        if released:
            logger.info(
                "Released %s dedicated proxies for owner %s",
                released,
                getattr(owner, "id", None) or owner,
            )
        return released

    @staticmethod
    def allocated_proxies(owner):
        """Return a queryset of proxies currently allocated to the owner."""
        return ProxyServer.objects.filter(
            dedicated_allocation__in=DedicatedProxyAllocation.objects.for_owner(owner)
        )

    @staticmethod
    def allocated_count(owner) -> int:
        return DedicatedProxyService.allocated_proxies(owner).count()

    @staticmethod
    def available_proxies():
        """Return a queryset of available dedicated proxies."""
        busy_proxy_ids = DedicatedProxyAllocation.objects.values("proxy_id")
        return ProxyServer.objects.filter(
            is_active=True,
            is_dedicated=True,
        ).exclude(id__in=busy_proxy_ids)

    @staticmethod
    def available_count() -> int:
        return DedicatedProxyService.available_proxies().count()

    @staticmethod
    def request_capacity(owner, quantity: int) -> None:
        """Placeholder hook for future automated provisioning workflows."""
        logger.info(
            "Provisioning placeholder: request %s dedicated proxies for owner %s",
            quantity,
            getattr(owner, "id", None) or owner,
        )


def is_multi_assign_enabled() -> bool:
    """Return whether a dedicated IP can be shared across multiple agents."""
    return getattr(settings, "DEDICATED_IP_ALLOW_MULTI_ASSIGN", True)
