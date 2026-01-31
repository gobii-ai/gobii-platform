from django.conf import settings

from api.services.system_settings import get_account_allow_registration

def global_settings_context(request):
    """Adds the Django settings object to the template context."""
    return {'settings': settings}


def account_allow_registration(request):
    """Expose dynamic signup availability for templates."""
    return {"account_allow_registration": get_account_allow_registration()}
