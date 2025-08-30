from django.conf import settings

def global_settings_context(request):
    """Adds the Django settings object to the template context."""
    return {'settings': settings} 