from django.utils import timezone

from observability import traced
from util.analytics import Analytics
from rest_framework import authentication, exceptions
from .models import ApiKey

class APIKeyAuthentication(authentication.BaseAuthentication):
    keyword = "X-Api-Key"

    def authenticate(self, request):
        with traced("API Authenticate") as span:
            raw_key = request.headers.get(self.keyword)
            if not raw_key:
                return None  # let other auth methods try (Session for admin)

            prefix = raw_key[:8]
            with traced("API Key Lookup") as span:
                try:
                    api_key = ApiKey.objects.select_related("user").get(prefix=prefix)
                except ApiKey.DoesNotExist:
                    raise exceptions.AuthenticationFailed("Invalid API key")

                if not api_key.matches(raw_key):
                    raise exceptions.AuthenticationFailed("Invalid API key")

            with traced("API Key Update"):
                api_key.last_used_at = timezone.now()
                api_key.save(update_fields=["last_used_at"])

            ip = Analytics.get_client_ip(request)

            Analytics.identify(api_key.user.id, {
                'email': api_key.user.email,
                'first_name': api_key.user.first_name,
                'last_name': api_key.user.last_name,
                'username': api_key.user.username,
                'date_joined': api_key.user.date_joined,
                'is_staff': api_key.user.is_staff,
                'is_superuser': api_key.user.is_superuser,
                'is_active': api_key.user.is_active,
                '$ip': ip,
                'ip': ip
            })

            return (api_key.user, None)
