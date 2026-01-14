"""Helpers for sharing console context information outside of the mixins.

The console relies on a session-scoped "context" to decide whether the user
is operating in their personal workspace or on behalf of an organization. This
module centralises the logic for resolving that context so template views and
other helpers can consume the same data shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.contrib.auth.models import AbstractBaseUser

from api.models import OrganizationMembership


@dataclass(frozen=True)
class ConsoleContext:
    type: str
    id: str
    name: str


@dataclass(frozen=True)
class ConsoleContextInfo:
    current_context: ConsoleContext
    current_membership: Optional[OrganizationMembership]
    can_manage_org_agents: bool


_ALLOWED_MANAGE_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
}


def build_console_context(request) -> ConsoleContextInfo:
    """Resolve the active console context for a request.

    Fallback rules mirror ``ConsoleContextMixin`` so views outside the console
    (e.g. the home page) can surface the same ownership information.
    """
    user: AbstractBaseUser = request.user
    default_name = user.get_full_name() or user.username or user.email or "Personal"

    override = getattr(request.session, "_context_override", None) if hasattr(request, "session") else None
    if isinstance(override, dict):
        override_type = str(override.get("type") or "").strip().lower()
        override_id = str(override.get("id") or "").strip()
        if override_type == "personal" and override_id == str(user.id):
            current_context = ConsoleContext(
                type="personal",
                id=str(user.id),
                name=default_name,
            )
            return ConsoleContextInfo(
                current_context=current_context,
                current_membership=None,
                can_manage_org_agents=True,
            )
        if override_type == "organization" and override_id:
            membership = (
                OrganizationMembership.objects.select_related("org")
                .filter(
                    user=user,
                    org_id=override_id,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                )
                .first()
            )
            if membership:
                current_context = ConsoleContext(
                    type="organization",
                    id=str(membership.org.id),
                    name=membership.org.name,
                )
                return ConsoleContextInfo(
                    current_context=current_context,
                    current_membership=membership,
                    can_manage_org_agents=membership.role in _ALLOWED_MANAGE_ROLES,
                )

    context_type = request.session.get("context_type", "personal")
    context_id = request.session.get("context_id", str(user.id))
    context_name = request.session.get("context_name", default_name)

    membership: Optional[OrganizationMembership] = None
    can_manage_org_agents = True

    if context_type == "organization":
        try:
            membership = (
                OrganizationMembership.objects.select_related("org")
                .get(
                    user=user,
                    org_id=context_id,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                )
            )
            context_name = membership.org.name
            context_id = str(membership.org.id)
            can_manage_org_agents = membership.role in _ALLOWED_MANAGE_ROLES
        except OrganizationMembership.DoesNotExist:
            # Fallback to personal context when membership is no longer valid.
            context_type = "personal"
            context_id = str(user.id)
            context_name = default_name
            membership = None
            can_manage_org_agents = True

    current_context = ConsoleContext(
        type=context_type,
        id=str(context_id),
        name=context_name,
    )

    return ConsoleContextInfo(
        current_context=current_context,
        current_membership=membership,
        can_manage_org_agents=can_manage_org_agents,
    )
