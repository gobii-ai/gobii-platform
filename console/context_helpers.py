"""Helpers for sharing console context information outside of the mixins.

The console relies on a session-scoped "context" to decide whether the user
is operating in their personal workspace or on behalf of an organization. This
module centralises the logic for resolving that context so template views and
other helpers can consume the same data shape.
"""

from dataclasses import dataclass
from typing import Optional

from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import PermissionDenied

from api.models import OrganizationMembership
from console.context_overrides import get_context_override


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


def _get_active_membership(user: AbstractBaseUser, org_id: str | None) -> Optional[OrganizationMembership]:
    if not org_id:
        return None
    return OrganizationMembership.objects.select_related("org").filter(
        user=user,
        org_id=org_id,
        status=OrganizationMembership.OrgStatus.ACTIVE,
    ).first()


def resolve_console_context(
    user: AbstractBaseUser,
    session,
    override: dict | None = None,
) -> ConsoleContextInfo:
    default_name = user.get_full_name() or user.username or user.email or "Personal"

    if override:
        context_type = str(override.get("type") or "").strip().lower()
        context_id = str(override.get("id") or "").strip()
        if not context_type or not context_id:
            raise PermissionDenied("Invalid context override.")
        if context_type == "personal":
            if str(user.id) != context_id:
                raise PermissionDenied("Invalid personal context override.")
            context = ConsoleContext(type="personal", id=str(user.id), name=default_name)
            return ConsoleContextInfo(
                current_context=context,
                current_membership=None,
                can_manage_org_agents=True,
            )
        if context_type == "organization":
            membership = _get_active_membership(user, context_id)
            if not membership:
                raise PermissionDenied("Invalid organization context override.")
            context = ConsoleContext(
                type="organization",
                id=str(membership.org.id),
                name=membership.org.name,
            )
            return ConsoleContextInfo(
                current_context=context,
                current_membership=membership,
                can_manage_org_agents=membership.role in _ALLOWED_MANAGE_ROLES,
            )
        raise PermissionDenied("Invalid context override.")

    context_type = (session or {}).get("context_type", "personal") if session is not None else "personal"
    context_id = (session or {}).get("context_id", str(user.id))
    context_name = (session or {}).get("context_name", default_name)

    membership: Optional[OrganizationMembership] = None
    can_manage_org_agents = True

    if context_type == "organization":
        membership = _get_active_membership(user, context_id)
        if membership:
            context_name = membership.org.name
            context_id = str(membership.org.id)
            can_manage_org_agents = membership.role in _ALLOWED_MANAGE_ROLES
        else:
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


def build_console_context(request) -> ConsoleContextInfo:
    """Resolve the active console context for a request.

    Fallback rules mirror ``ConsoleContextMixin`` so views outside the console
    (e.g. the home page) can surface the same ownership information.
    """
    user: AbstractBaseUser = request.user
    override = get_context_override(request)
    if override:
        try:
            return resolve_console_context(user, request.session, override=override)
        except PermissionDenied:
            pass
    return resolve_console_context(user, request.session)
