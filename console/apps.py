from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'console'

    def ready(self):  # noqa: D401 - init hook
        # Import signal handlers for realtime agent chat updates
        from .agent_chat import signals  # pylint: disable=unused-import
