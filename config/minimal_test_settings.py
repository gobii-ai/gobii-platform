"""
Minimal test settings that only test the new models.
"""
import os

# Set environment variables
os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SEGMENT_WRITE_KEY", "")

# Minimal Django settings
SECRET_KEY = "test-secret-key"
DEBUG = True
USE_TZ = True
USE_I18N = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'api',
]

# Required for tests
AUTH_USER_MODEL = 'auth.User'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'