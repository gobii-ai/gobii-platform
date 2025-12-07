from dataclasses import dataclass

from django.apps import apps
from django.db.models import F, IntegerField, Sum
from django.db.models.functions import Coalesce


@dataclass(frozen=True)
class AddonUplift:
    task_credits: int = 0
    contact_cap: int = 0


class AddonEntitlementService:
    """Helpers for aggregating active add-on entitlements."""

    @staticmethod
    def _get_model():
        return apps.get_model("api", "AddonEntitlement")

    @staticmethod
    def _active_entitlements(owner, at_time=None):
        model = AddonEntitlementService._get_model()
        qs = model.objects.all()
        qs = qs.for_owner(owner)
        return qs.active(at_time)

    @staticmethod
    def get_uplift(owner, at_time=None) -> AddonUplift:
        entitlements = AddonEntitlementService._active_entitlements(owner, at_time)

        aggregates = entitlements.aggregate(
            task_credits=Coalesce(
                Sum(
                    F("task_credits_delta") * F("quantity"),
                    output_field=IntegerField(),
                ),
                0,
            ),
            contact_cap=Coalesce(
                Sum(
                    F("contact_cap_delta") * F("quantity"),
                    output_field=IntegerField(),
                ),
                0,
            ),
        )

        return AddonUplift(
            task_credits=int(aggregates.get("task_credits", 0) or 0),
            contact_cap=int(aggregates.get("contact_cap", 0) or 0),
        )

    @staticmethod
    def get_task_credit_uplift(owner, at_time=None) -> int:
        return AddonEntitlementService.get_uplift(owner, at_time).task_credits

    @staticmethod
    def get_contact_cap_uplift(owner, at_time=None) -> int:
        return AddonEntitlementService.get_uplift(owner, at_time).contact_cap
