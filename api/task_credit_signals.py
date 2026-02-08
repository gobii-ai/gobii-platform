import logging

from django.apps import apps
from django.db.models.signals import post_delete, post_save

from pages.account_info_cache import invalidate_account_info_cache

logger = logging.getLogger(__name__)


def _invalidate_for_task_credit(instance) -> None:
    user_id = getattr(instance, "user_id", None)
    if user_id:
        invalidate_account_info_cache(user_id)

    org_id = getattr(instance, "organization_id", None)
    if org_id:
        try:
            from console.home_metrics import invalidate_console_home_metrics_cache

            invalidate_console_home_metrics_cache("organization", org_id)
        except Exception:
            logger.exception("Failed to invalidate console home metrics cache for org %s", org_id)


def _on_task_credit_saved(sender, instance, **kwargs) -> None:
    _invalidate_for_task_credit(instance)


def _on_task_credit_deleted(sender, instance, **kwargs) -> None:
    _invalidate_for_task_credit(instance)


def _on_user_billing_saved(sender, instance, **kwargs) -> None:
    user_id = getattr(instance, "user_id", None)
    if user_id:
        invalidate_account_info_cache(user_id)


def _on_org_billing_saved(sender, instance, **kwargs) -> None:
    org_id = getattr(instance, "organization_id", None)
    if not org_id:
        return
    try:
        from console.home_metrics import invalidate_console_home_metrics_cache

        invalidate_console_home_metrics_cache("organization", org_id)
    except Exception:
        logger.exception("Failed to invalidate console home metrics cache for org %s", org_id)


def register_task_credit_cache_invalidation() -> None:
    """
    Register signal handlers that invalidate home-page caches when billing state changes.
    """
    TaskCredit = apps.get_model("api", "TaskCredit")
    UserBilling = apps.get_model("api", "UserBilling")
    OrganizationBilling = apps.get_model("api", "OrganizationBilling")

    post_save.connect(
        _on_task_credit_saved,
        sender=TaskCredit,
        dispatch_uid="task_credit_cache_invalidate_post_save",
    )
    post_delete.connect(
        _on_task_credit_deleted,
        sender=TaskCredit,
        dispatch_uid="task_credit_cache_invalidate_post_delete",
    )

    post_save.connect(
        _on_user_billing_saved,
        sender=UserBilling,
        dispatch_uid="user_billing_cache_invalidate_post_save",
    )
    post_save.connect(
        _on_org_billing_saved,
        sender=OrganizationBilling,
        dispatch_uid="org_billing_cache_invalidate_post_save",
    )

