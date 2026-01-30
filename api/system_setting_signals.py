from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from api.models import SystemSetting
from api.services.system_settings import invalidate_system_settings_cache


@receiver(post_save, sender=SystemSetting)
@receiver(post_delete, sender=SystemSetting)
def _system_setting_changed(**_kwargs) -> None:
    invalidate_system_settings_cache()
