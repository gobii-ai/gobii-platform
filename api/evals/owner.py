"""Dedicated owner helpers for eval execution."""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model

from api.models import Organization, OrganizationMembership


EVAL_RUNNER_USERNAME = "eval_runner"
EVAL_RUNNER_EMAIL = "eval@localhost"
EVAL_RUNNER_ORG_SLUG = "eval-runner"


def ensure_eval_runner_user_and_owner(*, minimum_seats: int = 1, stdout=None):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=EVAL_RUNNER_USERNAME,
        defaults={"email": EVAL_RUNNER_EMAIL},
    )
    if user.email != EVAL_RUNNER_EMAIL:
        user.email = EVAL_RUNNER_EMAIL
        user.save(update_fields=["email"])

    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"verified": True, "primary": True},
    )
    organization = ensure_eval_runner_owner(
        user,
        minimum_seats=minimum_seats,
        stdout=stdout,
    )
    return user, organization


def ensure_eval_runner_owner(user, *, minimum_seats: int = 1, stdout=None) -> Organization:
    organization, _ = Organization.objects.get_or_create(
        slug=EVAL_RUNNER_ORG_SLUG,
        defaults={
            "name": "Eval Runner",
            "created_by": user,
            "is_active": True,
        },
    )
    changed_fields = []
    if organization.created_by_id != user.id:
        organization.created_by = user
        changed_fields.append("created_by")
    if not organization.is_active:
        organization.is_active = True
        changed_fields.append("is_active")
    if changed_fields:
        organization.save(update_fields=[*changed_fields, "updated_at"])

    OrganizationMembership.objects.update_or_create(
        org=organization,
        user=user,
        defaults={
            "role": OrganizationMembership.OrgRole.OWNER,
            "status": OrganizationMembership.OrgStatus.ACTIVE,
        },
    )

    billing = organization.billing
    target_seats = max(1, int(minimum_seats or 1))
    if billing.purchased_seats < target_seats:
        billing.purchased_seats = target_seats
        billing.save(update_fields=["purchased_seats", "updated_at"])
        if stdout:
            stdout.write(f"Reserved {target_seats} eval-runner organization seat(s).")

    return organization
