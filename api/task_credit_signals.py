from django.apps import apps
from django.db.models.signals import post_delete, post_save

from pages.account_info_cache import invalidate_account_info_cache


def _invalidate_user_cache(instance) -> None:
    """Invalidate user-scoped caches based on instance.user_id."""
    user_id = getattr(instance, "user_id", None)
    if user_id:
        invalidate_account_info_cache(user_id)


def _invalidate_for_task_credit(instance) -> None:
    _invalidate_user_cache(instance)


def _on_task_credit_saved(sender, instance, **kwargs) -> None:
    _invalidate_for_task_credit(instance)


def _on_task_credit_deleted(sender, instance, **kwargs) -> None:
    _invalidate_for_task_credit(instance)


def _on_user_billing_saved(sender, instance, **kwargs) -> None:
    _invalidate_user_cache(instance)


def register_task_credit_cache_invalidation() -> None:
    """
    Register signal handlers that invalidate account-info caches when billing state changes.
    """
    TaskCredit = apps.get_model("api", "TaskCredit")
    UserBilling = apps.get_model("api", "UserBilling")

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
