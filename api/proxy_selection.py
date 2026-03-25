"""
HTTP Proxy Selection

This module provides proxy selection logic for browser automation tasks.
It handles intelligent proxy selection based on health checks, preferences,
and fallback strategies.
"""

import logging
from datetime import timedelta
from typing import Any, Callable, Iterable, Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _normalize_allowed_proxy_types(allowed_proxy_types: Optional[Iterable[str]]) -> Optional[set[str]]:
    if allowed_proxy_types is None:
        return None
    normalized = {
        str(proxy_type).strip().upper()
        for proxy_type in allowed_proxy_types
        if str(proxy_type).strip()
    }
    return normalized or None


def _proxy_type_allowed(proxy_server: Any, allowed_proxy_types: Optional[set[str]]) -> bool:
    if proxy_server is None or allowed_proxy_types is None:
        return True
    proxy_type = str(getattr(proxy_server, "proxy_type", "") or "").strip().upper()
    return proxy_type in allowed_proxy_types


def _select_random_proxy_from_shared_pool(
    *,
    health_check_days: int,
    allowed_proxy_types: Optional[set[str]],
):
    from .models import BrowserUseAgent

    recent_cutoff = timezone.now() - timedelta(days=health_check_days)
    available_proxies = BrowserUseAgent._shared_proxy_queryset()
    if allowed_proxy_types is not None:
        available_proxies = available_proxies.filter(proxy_type__in=sorted(allowed_proxy_types))

    healthy_static_proxy = (
        available_proxies.filter(
            static_ip__isnull=False,
            health_check_results__status="PASSED",
            health_check_results__checked_at__gte=recent_cutoff,
        )
        .distinct()
        .order_by("?")
        .first()
    )
    if healthy_static_proxy:
        return healthy_static_proxy

    healthy_proxy = (
        available_proxies.filter(
            health_check_results__status="PASSED",
            health_check_results__checked_at__gte=recent_cutoff,
        )
        .distinct()
        .order_by("?")
        .first()
    )
    if healthy_proxy:
        return healthy_proxy

    static_ip_proxy = available_proxies.filter(static_ip__isnull=False).exclude(static_ip="").order_by("?").first()
    if static_ip_proxy:
        return static_ip_proxy

    return available_proxies.order_by("?").first()


def proxy_has_recent_health_pass(proxy_server, health_check_days: int = 45) -> bool:
    """
    Check if a proxy has a recent successful health check.
    
    Args:
        proxy_server: ProxyServer instance to check
        health_check_days: Number of days to consider "recent" (default: 45)
        
    Returns:
        True if proxy has successful health check within the specified days
    """
    recent_cutoff = timezone.now() - timedelta(days=health_check_days)
    return proxy_server.health_check_results.filter(
        status="PASSED", checked_at__gte=recent_cutoff
    ).exists()


def select_proxy(
    preferred_proxy: Optional = None,
    override_proxy: Optional = None,
    allow_no_proxy_in_debug: bool = False,
    health_check_days: int = 45,
    context_id: Optional[str] = None,
    allowed_proxy_types: Optional[Iterable[str]] = None,
) -> Optional:
    """
    Select appropriate proxy based on preferences and health checks.
    
    Args:
        preferred_proxy: Preferred proxy to use if healthy
        override_proxy: Override proxy (takes highest priority)
        allow_no_proxy_in_debug: Allow returning None in DEBUG mode when no proxy available
        health_check_days: Number of days to consider for health check recency
        context_id: Optional context identifier for logging (e.g., task_id, agent_id)
        
    Returns:
        Selected ProxyServer instance or None
        
    Raises:
        RuntimeError: When no proxy is available and DEBUG=False
    """
    from .models import BrowserUseAgent, ProxyServer
    
    context_desc = f" for {context_id}" if context_id else ""
    normalized_allowed_proxy_types = _normalize_allowed_proxy_types(allowed_proxy_types)
    
    # Priority 1: Override proxy (for testing/specific needs)
    if override_proxy:
        if _proxy_type_allowed(override_proxy, normalized_allowed_proxy_types):
            logger.info("Using override proxy%s: %s", context_desc, override_proxy)
            return override_proxy
        logger.warning(
            "Ignoring override proxy with disallowed type%s: %s",
            context_desc,
            override_proxy,
        )
    
    # Priority 2: Preferred proxy (if healthy)
    if preferred_proxy:
        if not _proxy_type_allowed(preferred_proxy, normalized_allowed_proxy_types):
            logger.info(
                "Preferred proxy has disallowed type%s, selecting alternative: %s",
                context_desc,
                preferred_proxy,
            )
            preferred_proxy = None
        elif proxy_has_recent_health_pass(preferred_proxy, health_check_days):
            logger.info(
                "Using preferred proxy (recently healthy)%s: %s",
                context_desc,
                preferred_proxy
            )
            return preferred_proxy
        else:
            logger.info(
                "Preferred proxy unhealthy%s, selecting alternative: %s",
                context_desc,
                preferred_proxy
            )
            
            # Try to find a healthy alternative
            if normalized_allowed_proxy_types is None:
                alternative_proxy = BrowserUseAgent.select_random_proxy()
            else:
                alternative_proxy = _select_random_proxy_from_shared_pool(
                    health_check_days=health_check_days,
                    allowed_proxy_types=normalized_allowed_proxy_types,
                )
            if alternative_proxy:
                logger.info(
                    "Using healthy alternative proxy%s: %s",
                    context_desc,
                    alternative_proxy
                )
                return alternative_proxy
            else:
                logger.warning(
                    "No healthy alternatives available%s; falling back to preferred proxy: %s",
                    context_desc,
                    preferred_proxy
                )
                return preferred_proxy
    
    # Priority 3: Health-aware random selection
    if normalized_allowed_proxy_types is None:
        proxy_server = BrowserUseAgent.select_random_proxy()
    else:
        proxy_server = _select_random_proxy_from_shared_pool(
            health_check_days=health_check_days,
            allowed_proxy_types=normalized_allowed_proxy_types,
        )
    if proxy_server:
        logger.info("Using health-aware selected proxy%s: %s", context_desc, proxy_server)
        return proxy_server
    
    # No proxy available
    debug_mode = getattr(settings, "DEBUG", False)
    community_mode = not getattr(settings, "GOBII_PROPRIETARY_MODE", False)

    if allow_no_proxy_in_debug and debug_mode:
        logger.warning("No proxy available%s. Continuing without proxy in debug mode.", context_desc)
        return None

    if community_mode:
        logger.warning(
            "No proxy available%s. Continuing without proxy in community mode.",
            context_desc,
        )
        return None

    error_msg = (
        f"No proxy available{context_desc} and proxies are required in proprietary mode."
    )
    logger.error(error_msg)
    raise RuntimeError(error_msg)


def select_proxy_for_persistent_agent(persistent_agent, override_proxy: Optional = None, **kwargs) -> Optional:
    """
    Select proxy for a persistent agent.
    
    Args:
        persistent_agent: PersistentAgent instance
        override_proxy: Optional override proxy
        **kwargs: Additional arguments passed to select_proxy()
        
    Returns:
        Selected ProxyServer instance or None
    """
    # Extract preferred proxy if the persistent agent has one
    # This assumes PersistentAgent might have a preferred_proxy field
    preferred_proxy = getattr(persistent_agent, 'preferred_proxy', None)
    return select_proxy(
        preferred_proxy=preferred_proxy,
        override_proxy=override_proxy,
        context_id=f"persistent_agent_{persistent_agent.id}",
        **kwargs
    )


def select_proxy_for_browser_task(task_obj, override_proxy: Optional = None, **kwargs) -> Optional:
    """
    Select proxy for a browser use agent task.
    
    Args:
        task_obj: BrowserUseAgentTask instance
        override_proxy: Optional override proxy
        **kwargs: Additional arguments passed to select_proxy()
        
    Returns:
        Selected ProxyServer instance or None
    """
    # Extract preferred proxy from the task's agent
    preferred_proxy = None
    if task_obj.agent and hasattr(task_obj.agent, 'preferred_proxy'):
        preferred_proxy = task_obj.agent.preferred_proxy
    
    return select_proxy(
        preferred_proxy=preferred_proxy,
        override_proxy=override_proxy,
        context_id=f"task_{task_obj.id}",
        **kwargs
    )


def select_proxies_for_webhook(
    context_obj: Any,
    selector: Callable[[Any], Any],
    *,
    log_context: str,
) -> tuple[dict[str, str] | None, str | None]:
    """
    Shared helper for selecting proxies for webhook delivery.

    Returns a Requests-style proxies mapping or an error message if selection failed.
    """
    try:
        proxy_server = selector(context_obj, allow_no_proxy_in_debug=False)
    except RuntimeError as exc:
        logger.error("Webhook proxy selection failed for %s: %s", log_context, exc)
        return None, str(exc)

    if not proxy_server:
        message = "No proxy server available for webhook delivery"
        logger.warning("Webhook proxy unavailable for %s", log_context)
        return None, message

    proxy_url = proxy_server.proxy_url
    logger.info(
        "Using proxy %s:%s for webhook delivery on %s",
        proxy_server.host,
        proxy_server.port,
        log_context,
    )
    return {"http": proxy_url, "https": proxy_url}, None
