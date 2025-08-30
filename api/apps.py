from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        """Import webhooks so event handlers get registered."""
        try:
            from . import webhooks  # noqa: F401  # pragma: no cover
        except ImportError as e:  # pragma: no cover - optional dependency
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to import webhooks: {e}")
