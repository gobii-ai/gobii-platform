from django.apps import AppConfig
import api.spectacular_extensions


# NOTE: See SPECTACULAR_SETTINGS in settings.py for lots of base configuration

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

        # Import idle notifications to wire Redis notify on IMAP account changes
        try:
            from . import idle_notifications  # noqa: F401  # pragma: no cover
        except Exception as e:  # pragma: no cover - optional dependency
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to import idle_notifications: {e}")

        try:
            from .agent import peer_link_signals  # noqa: F401  # pragma: no cover
        except Exception as e:  # pragma: no cover - optional dependency
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to import peer_link_signals: {e}")
