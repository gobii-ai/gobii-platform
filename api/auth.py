from django.utils import timezone

from observability import traced
from util.analytics import Analytics
from util.trial_enforcement import (
    PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE,
    can_user_use_personal_agents_and_api,
)
from rest_framework import authentication, exceptions
from .models import ApiKey, OrganizationMembership

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
                    api_key = (
                        ApiKey.objects.select_related("user", "organization", "created_by")
                        .get(prefix=prefix)
                    )
                except ApiKey.DoesNotExist:
                    raise exceptions.AuthenticationFailed("Invalid API key")

                if not api_key.matches(raw_key):
                    raise exceptions.AuthenticationFailed("Invalid API key")

            acting_user = self._determine_acting_user(api_key)

            if not acting_user or not acting_user.is_active:
                raise exceptions.AuthenticationFailed("User account is inactive")

            with traced("API Key Update"):
                api_key.last_used_at = timezone.now()
                api_key.save(update_fields=["last_used_at"])

            ip = Analytics.get_client_ip(request)

            Analytics.identify(acting_user.id, {
                'email': acting_user.email,
                'first_name': acting_user.first_name,
                'last_name': acting_user.last_name,
                'username': acting_user.username,
                'date_joined': acting_user.date_joined,
                'is_staff': acting_user.is_staff,
                'is_superuser': acting_user.is_superuser,
                'is_active': acting_user.is_active,
                '$ip': ip,
                'ip': ip
            })

            return (acting_user, api_key)

    def _determine_acting_user(self, api_key: ApiKey):
        """Return the user that should be treated as the authenticated principal."""

        if api_key.organization_id:
            org = api_key.organization
            if not org or not org.is_active:
                raise exceptions.AuthenticationFailed("Organization is inactive")

            created_by = api_key.created_by
            if created_by and created_by.is_active:
                if OrganizationMembership.objects.filter(
                    org=org,
                    user=created_by,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                ).exists():
                    return created_by

            fallback_membership = (
                OrganizationMembership.objects.select_related("user")
                .filter(
                    org=org,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                    role__in=[
                        OrganizationMembership.OrgRole.OWNER,
                        OrganizationMembership.OrgRole.ADMIN,
                    ],
                )
                .order_by('role', 'user__date_joined')
                .first()
            )

            if fallback_membership and fallback_membership.user and fallback_membership.user.is_active:
                return fallback_membership.user

            raise exceptions.AuthenticationFailed("Organization has no active members")

        if not api_key.user:
            raise exceptions.AuthenticationFailed("API key is missing an owner")

        if not api_key.user.is_active:
            raise exceptions.AuthenticationFailed("User account is inactive")

        if not can_user_use_personal_agents_and_api(api_key.user):
            raise exceptions.AuthenticationFailed(PERSONAL_USAGE_REQUIRES_TRIAL_MESSAGE)

        return api_key.user
