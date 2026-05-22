import logging
import smtplib
import uuid
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from anymail.exceptions import AnymailError
from waffle import flag_is_active

from api.models import Organization, OrganizationInvite, OrganizationMembership
from console.api_helpers import _parse_json_body as _parse_json_body_or_raise
from console.context_helpers import build_console_context
from console.forms import OrganizationForm, OrganizationInviteForm
from console.role_constants import BILLING_MANAGE_ROLES, MEMBER_MANAGE_ROLES
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)

OWNER_EQUIVALENT_ROLES = (
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
)


def _json_error(message: str, *, status: int = 400):
    return JsonResponse({"error": message}, status=status)


def _json_field_errors(errors, *, status: int = 400):
    return JsonResponse(
        {
            "errors": {
                field: [str(message) for message in messages]
                for field, messages in errors.items()
            },
        },
        status=status,
    )


def _parse_json_body(request):
    try:
        payload = _parse_json_body_or_raise(request)
    except ValueError as exc:
        return None, _json_field_errors({"__all__": [str(exc)]})
    return payload, None


def _resolve_allowed_role_choices_for_role(role: str | None) -> list[tuple[str, str]]:
    all_role_choices = list(OrganizationMembership.OrgRole.choices)
    if role in OWNER_EQUIVALENT_ROLES:
        return all_role_choices
    if role == OrganizationMembership.OrgRole.ADMIN:
        return [
            choice
            for choice in all_role_choices
            if choice[0] not in OWNER_EQUIVALENT_ROLES
        ]
    return []


def _active_invites(org: Organization):
    return OrganizationInvite.objects.filter(
        org=org,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gte=timezone.now(),
    ).select_related("invited_by").order_by("email")


def _serialize_member(membership: OrganizationMembership, viewer_membership: OrganizationMembership) -> dict:
    user = membership.user
    display_name = user.get_full_name() or user.username or user.email or "Member"
    allowed_role_values = {
        value
        for value, _label in _resolve_allowed_role_choices_for_role(viewer_membership.role)
    }
    can_manage = viewer_membership.role in MEMBER_MANAGE_ROLES
    target_owner_equivalent = membership.role in OWNER_EQUIVALENT_ROLES
    viewer_is_admin = viewer_membership.role == OrganizationMembership.OrgRole.ADMIN
    return {
        "userId": str(user.id),
        "name": display_name,
        "email": user.email or "",
        "role": membership.role,
        "roleLabel": membership.get_role_display(),
        "isCurrentUser": user.id == viewer_membership.user_id,
        "canUpdateRole": can_manage and membership.role in allowed_role_values,
        "canRemove": (
            can_manage
            and user.id != viewer_membership.user_id
            and not (viewer_is_admin and target_owner_equivalent)
        ),
    }


def _serialize_invite(invite: OrganizationInvite) -> dict:
    return {
        "token": invite.token,
        "email": invite.email,
        "role": invite.role,
        "roleLabel": invite.get_role_display(),
        "invitedBy": invite.invited_by.email or invite.invited_by.username,
        "sentAt": invite.sent_at.isoformat() if invite.sent_at else None,
        "expiresAt": invite.expires_at.isoformat() if invite.expires_at else None,
    }


def _serialize_organization(org: Organization, membership: OrganizationMembership) -> dict:
    role_choices = _resolve_allowed_role_choices_for_role(membership.role)
    billing = getattr(org, "billing", None)
    return {
        "organization": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "plan": org.plan,
        },
        "viewer": {
            "role": membership.role,
            "roleLabel": membership.get_role_display(),
            "canEditOrganization": membership.role in OWNER_EQUIVALENT_ROLES,
            "canManageMembers": membership.role in MEMBER_MANAGE_ROLES,
            "canManageBilling": membership.role in BILLING_MANAGE_ROLES,
        },
        "roles": [
            {"value": value, "label": label}
            for value, label in role_choices
        ],
        "members": [
            _serialize_member(member, membership)
            for member in OrganizationMembership.objects.filter(
                org=org,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).select_related("user").order_by("user__email")
        ],
        "pendingInvites": [
            _serialize_invite(invite)
            for invite in _active_invites(org)
        ],
        "billing": {
            "purchasedSeats": getattr(billing, "purchased_seats", None),
            "seatsReserved": getattr(billing, "seats_reserved", None),
            "seatsAvailable": getattr(billing, "seats_available", None),
        } if billing else None,
    }


def _resolve_current_org(request):
    if not flag_is_active(request, "organizations"):
        raise PermissionDenied("Organizations are not available.")
    resolved = build_console_context(request)
    if resolved.current_context.type != "organization" or not resolved.current_membership:
        return None, None
    return resolved.current_membership.org, resolved.current_membership


def _require_current_org(request):
    org, membership = _resolve_current_org(request)
    if not org or not membership:
        return None, None, _json_error("Switch to an organization context first.", status=404)
    return org, membership, None


def _lock_organization(org: Organization) -> Organization:
    return Organization.objects.select_for_update().get(pk=org.pk)


def _send_invitation_email(request, org: Organization, invite: OrganizationInvite) -> None:
    accept_url = request.build_absolute_uri(
        f"/app/organizations/invites/{invite.token}/accept"
    )
    reject_url = request.build_absolute_uri(
        reverse("org_invite_reject", kwargs={"token": invite.token})
    )
    context = {
        "org": org,
        "invited_by": request.user,
        "invite": invite,
        "accept_url": accept_url,
        "reject_url": reject_url,
    }
    html_body = render_to_string("emails/organization_invite.html", context)
    text_body = render_to_string("emails/organization_invite.txt", context)
    subject = f"You're invited to join {org.name} on Gobii"
    send_mail(
        subject=subject,
        message=text_body,
        from_email=None,
        recipient_list=[invite.email],
        html_message=html_body,
        fail_silently=False,
    )


class CurrentOrganizationAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "patch"]

    def get(self, request):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        return JsonResponse(_serialize_organization(org, membership))

    @transaction.atomic
    def patch(self, request):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        if membership.role not in OWNER_EQUIVALENT_ROLES:
            return _json_error("You do not have permission to edit this organization.", status=403)

        payload, error = _parse_json_body(request)
        if error:
            return error
        form = OrganizationForm(data={"name": payload.get("name", "")}, instance=org)
        if not form.is_valid():
            return _json_field_errors(form.errors)

        previous_name = org.name
        org = form.save()
        request.session["context_type"] = "organization"
        request.session["context_id"] = str(org.id)
        request.session["context_name"] = org.name

        props = Analytics.with_org_properties(
            {
                "actor_id": str(request.user.id),
                "old_name": previous_name,
                "new_name": org.name,
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_UPDATED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        return JsonResponse(_serialize_organization(org, membership))


class CurrentOrganizationInviteAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        if membership.role not in MEMBER_MANAGE_ROLES:
            return _json_error("You do not have permission to manage members.", status=403)

        payload, error = _parse_json_body(request)
        if error:
            return error
        allowed_roles = _resolve_allowed_role_choices_for_role(membership.role)
        form = OrganizationInviteForm(
            data={
                "email": payload.get("email", ""),
                "role": payload.get("role", ""),
            },
            org=org,
            allowed_roles=allowed_roles,
        )
        billing = getattr(org, "billing", None)
        invite_role = str(payload.get("role") or "")
        if (
            billing
            and billing.seats_available <= 0
            and invite_role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
        ):
            form.add_error(None, "No seats available. Increase the seat count before inviting new members.")
        if not form.is_valid():
            return _json_field_errors(form.errors)

        invite = OrganizationInvite.objects.create(
            org=org,
            email=form.cleaned_data["email"],
            role=form.cleaned_data["role"],
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timedelta(days=7),
            invited_by=request.user,
        )
        props = Analytics.with_org_properties(
            {
                "invite_id": str(invite.id),
                "invite_token": invite.token,
                "invite_role": invite.role,
                "invite_email": invite.email,
                "actor_id": str(request.user.id),
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_INVITE_SENT,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        try:
            _send_invitation_email(request, org, invite)
        except (AnymailError, OSError, smtplib.SMTPException) as exc:
            logger.warning("Failed sending org invite email: %s", exc)

        return JsonResponse(_serialize_organization(org, membership), status=201)


class CurrentOrganizationInviteDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["delete"]

    @transaction.atomic
    def delete(self, request, token: str):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        if membership.role not in MEMBER_MANAGE_ROLES:
            return _json_error("You do not have permission to manage members.", status=403)

        invite = get_object_or_404(OrganizationInvite, org=org, token=token)
        if invite.accepted_at or invite.revoked_at:
            return _json_error("Invite is already finalized.", status=400)

        invite.revoked_at = timezone.now()
        invite.save(update_fields=["revoked_at"])
        props = Analytics.with_org_properties(
            {
                "invite_id": str(invite.id),
                "invite_token": invite.token,
                "actor_id": str(request.user.id),
                "reason": "revoked",
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_INVITE_DECLINED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        return JsonResponse(_serialize_organization(org, membership))


class CurrentOrganizationInviteResendAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request, token: str):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        if membership.role not in MEMBER_MANAGE_ROLES:
            return _json_error("You do not have permission to manage members.", status=403)

        invite = get_object_or_404(OrganizationInvite, org=org, token=token)
        if invite.accepted_at or invite.revoked_at or invite.expires_at < timezone.now():
            return _json_error("Invite is no longer valid.", status=400)

        invite.sent_at = timezone.now()
        invite.save(update_fields=["sent_at"])
        props = Analytics.with_org_properties(
            {
                "invite_id": str(invite.id),
                "invite_token": invite.token,
                "actor_id": str(request.user.id),
                "resend": True,
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_INVITE_SENT,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        try:
            _send_invitation_email(request, org, invite)
        except (AnymailError, OSError, smtplib.SMTPException) as exc:
            logger.warning("Failed resending org invite email: %s", exc)

        return JsonResponse(_serialize_organization(org, membership))


class CurrentOrganizationMemberAPIView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    @transaction.atomic
    def patch(self, request, user_id: int):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        if membership.role not in MEMBER_MANAGE_ROLES:
            return _json_error("You do not have permission to manage members.", status=403)

        payload, error = _parse_json_body(request)
        if error:
            return error
        new_role = str(payload.get("role") or "")
        allowed_role_values = {value for value, _label in _resolve_allowed_role_choices_for_role(membership.role)}
        if new_role not in allowed_role_values:
            return _json_error("Invalid role.", status=403)

        org = _lock_organization(org)
        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        if target_membership.role == new_role:
            return JsonResponse(_serialize_organization(org, membership))

        if (
            membership.role == OrganizationMembership.OrgRole.ADMIN
            and target_membership.role in OWNER_EQUIVALENT_ROLES
        ):
            return _json_error("Admins cannot modify owner-equivalent roles.", status=403)

        if (
            target_membership.role == OrganizationMembership.OrgRole.OWNER
            and new_role != OrganizationMembership.OrgRole.OWNER
            and OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count() <= 1
        ):
            return _json_error("You must keep at least one owner in the organization.", status=400)

        previous_role = target_membership.role
        target_membership.role = new_role
        target_membership.save(update_fields=["role"])
        props = Analytics.with_org_properties(
            {
                "member_id": str(target_membership.user_id),
                "actor_id": str(request.user.id),
                "old_role": previous_role,
                "new_role": new_role,
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_ROLE_UPDATED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        return JsonResponse(_serialize_organization(org, membership))

    @transaction.atomic
    def delete(self, request, user_id: int):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        if membership.role not in MEMBER_MANAGE_ROLES:
            return _json_error("You do not have permission to manage members.", status=403)
        if request.user.id == user_id:
            return _json_error("You cannot remove yourself.", status=400)

        org = _lock_organization(org)
        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        if (
            membership.role == OrganizationMembership.OrgRole.ADMIN
            and target_membership.role in OWNER_EQUIVALENT_ROLES
        ):
            return _json_error("Admins cannot remove owner-equivalent roles.", status=403)

        if (
            target_membership.role == OrganizationMembership.OrgRole.OWNER
            and OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count() <= 1
        ):
            return _json_error("You must keep at least one owner in the organization.", status=400)

        target_membership.status = OrganizationMembership.OrgStatus.REMOVED
        target_membership.save(update_fields=["status"])
        props = Analytics.with_org_properties(
            {
                "member_id": str(target_membership.user_id),
                "member_role": target_membership.role,
                "actor_id": str(request.user.id),
                "reason": "removed_by_admin",
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_REMOVED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        return JsonResponse(_serialize_organization(org, membership))
