from django.apps import AppConfig


class PagesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pages'

    def ready(self):
        # Import signals to ensure they are registered
        try:
            import pages.signals
        except ImportError as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to import signals: {e}")