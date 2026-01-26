"""
DRF permission classes for API access control.
"""

from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied
from api.services.email_verification import has_verified_email
from api.services.sandbox_access import has_sandbox_access


class IsEmailVerified(permissions.BasePermission):
    """
    Permission class that requires users to have a verified email address.

    Use this permission on API endpoints that involve external communications
    (email, SMS, webhooks) or other features that should be gated behind
    email verification.

    Superusers bypass this check.
    """

    message = "Email verification required to use this API feature. Please verify your email address in your account settings."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not has_verified_email(request.user):
            raise PermissionDenied(
                detail={
                    "detail": self.message,
                    "code": "email_verification_required",
                }
            )
        return True


class HasSandboxAccess(permissions.BasePermission):
    """Permission class that requires the sandbox waffle flag to be enabled."""

    message = "Sandbox access is not enabled for this account."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not has_sandbox_access(request.user):
            raise PermissionDenied(
                detail={
                    "detail": self.message,
                    "code": "sandbox_access_required",
                }
            )
        return True
