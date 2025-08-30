"""
HTTP Proxy Selection

This module provides proxy selection logic for browser automation tasks.
It handles intelligent proxy selection based on health checks, preferences,
and fallback strategies.
"""

import logging
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


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
    context_id: Optional[str] = None
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
    
    # Priority 1: Override proxy (for testing/specific needs)
    if override_proxy:
        logger.info("Using override proxy%s: %s", context_desc, override_proxy)
        return override_proxy
    
    # Priority 2: Preferred proxy (if healthy)
    if preferred_proxy:
        if proxy_has_recent_health_pass(preferred_proxy, health_check_days):
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
            alternative_proxy = BrowserUseAgent.select_random_proxy()
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
    proxy_server = BrowserUseAgent.select_random_proxy()
    if proxy_server:
        logger.info("Using health-aware selected proxy%s: %s", context_desc, proxy_server)
        return proxy_server
    
    # No proxy available
    if allow_no_proxy_in_debug and getattr(settings, "DEBUG", False):
        logger.warning("No proxy available%s. Continuing without proxy in debug mode.", context_desc)
        return None
    
    error_msg = f"No proxy available{context_desc} and DEBUG=False."
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