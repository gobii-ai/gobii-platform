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

        try:
            from .agent import collaborator_signals  # noqa: F401  # pragma: no cover
        except Exception as e:  # pragma: no cover - optional dependency
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to import collaborator_signals: {e}")

        try:
            from . import system_setting_signals  # noqa: F401  # pragma: no cover
        except ImportError as e:  # pragma: no cover - optional dependency
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to import system_setting_signals: {e}")

        try:
            from . import task_credit_signals  # pragma: no cover

            task_credit_signals.register_task_credit_cache_invalidation()
        except Exception as e:  # pragma: no cover - optional dependency
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to register task_credit_signals: {e}")
