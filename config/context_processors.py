from django.conf import settings

from api.services.system_settings import (
    get_account_allow_password_login,
    get_account_allow_password_signup,
    get_account_allow_social_login,
    get_account_allow_social_signup,
)

def global_settings_context(request):
    """Adds the Django settings object to the template context."""
    return {'settings': settings}


def account_auth_flags(request):
    """Expose dynamic auth availability for templates."""
    return {
        "account_allow_password_signup": get_account_allow_password_signup(),
        "account_allow_social_signup": get_account_allow_social_signup(),
        "account_allow_password_login": get_account_allow_password_login(),
        "account_allow_social_login": get_account_allow_social_login(),
    }
