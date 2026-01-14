from django.core.exceptions import PermissionDenied

from api.models import OrganizationMembership
from console.context_helpers import ConsoleContext

CONTEXT_TYPE_HEADER = "X-Gobii-Context-Type"
CONTEXT_ID_HEADER = "X-Gobii-Context-Id"


def get_context_override(request):
    if request is None:
        return None
    context_type = request.headers.get(CONTEXT_TYPE_HEADER) or request.GET.get("context_type")
    context_id = request.headers.get(CONTEXT_ID_HEADER) or request.GET.get("context_id")
    if not context_type or not context_id:
        return None
    return {"type": context_type, "id": context_id}


def resolve_context_override(user, override):
    if not override:
        return None, None
    context_type = str(override.get("type") or "").strip().lower()
    context_id = str(override.get("id") or "").strip()
    if not context_type or not context_id:
        raise PermissionDenied("Invalid context override.")
    if context_type == "personal":
        if str(user.id) != context_id:
            raise PermissionDenied("Invalid personal context override.")
        name = user.get_full_name() or user.username or user.email or "Personal"
        return ConsoleContext(type="personal", id=str(user.id), name=name), None
    if context_type == "organization":
        membership = OrganizationMembership.objects.select_related("org").filter(
            user=user,
            org_id=context_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).first()
        if not membership:
            raise PermissionDenied("Invalid organization context override.")
        return (
            ConsoleContext(type="organization", id=str(membership.org.id), name=membership.org.name),
            membership,
        )
    raise PermissionDenied("Invalid context override.")
