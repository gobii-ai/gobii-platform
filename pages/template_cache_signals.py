from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from api.models import PersistentAgentTemplate
from pages.homepage_cache import invalidate_homepage_pretrained_cache
from pages.library_views import invalidate_library_template_caches


def invalidate_public_template_caches() -> None:
    invalidate_homepage_pretrained_cache()
    invalidate_library_template_caches()


@receiver(
    [post_save, post_delete],
    sender=PersistentAgentTemplate,
    dispatch_uid="persistent_agent_template_public_cache_invalidation",
)
def invalidate_public_template_caches_after_change(**_kwargs) -> None:
    transaction.on_commit(invalidate_public_template_caches)
