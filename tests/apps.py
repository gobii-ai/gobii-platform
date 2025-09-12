from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _seed_llm(sender, **kwargs):  # noqa: ANN001
    try:
        from tests.utils.llm_seed import seed_persistent_basic
        import os
        # Only seed in test settings contexts
        if os.getenv('DJANGO_SETTINGS_MODULE', '').endswith('config.test_settings'):
            seed_persistent_basic(include_openrouter=True)
    except Exception:
        # Best effort; tests that need custom shape can seed explicitly
        pass


class TestsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tests'

    def ready(self):  # noqa: D401
        # Hook post_migrate to ensure LLM DB config exists for tests that expect it
        post_migrate.connect(_seed_llm, dispatch_uid='tests_seed_llm_db')

