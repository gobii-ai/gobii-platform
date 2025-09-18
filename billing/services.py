from datetime import date
from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.utils import timezone
import logging
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')

class BillingService:
    """
    A service for handling billing-related operations, particularly
    for calculating billing dates and periods based on a monthly cadence.
    """

    @staticmethod
    def _get_billing_record_for_owner(owner):
        """Return the billing record (user or organization) for the provided owner."""
        if owner is None:
            return None, "unknown"

        OrgModel = apps.get_model("api", "Organization")
        if isinstance(owner, OrgModel):
            OrgBilling = apps.get_model("api", "OrganizationBilling")
            record = OrgBilling.objects.filter(organization_id=owner.id).first()
            return record, "organization"

        UserBilling = apps.get_model("api", "UserBilling")
        record = UserBilling.objects.filter(user_id=owner.id).first()
        return record, "user"

    @staticmethod
    @tracer.start_as_current_span("BillingService validate_billing_day")
    def validate_billing_day(billing_day: int):
        if not (1 <= billing_day <= 31):
            raise ValueError("Billing day must be between 1 and 31.")

    @staticmethod
    @tracer.start_as_current_span("BillingService compute_next_billing_date")
    def compute_next_billing_date(billing_day: int, reference: date | None = None) -> date:
        """
        Compute the next billing date based on a given billing day (1–31) and a reference date.

        The next billing date is determined as follows:
        - If the billing day is today or in the future, return that date.
        - If the billing day has already passed this month, return the same day next month.
        - If the billing day is invalid (not between 1 and 31), raise a ValueError.

        Args:
        -----
            billing_day (int): The day of the month when billing occurs (1–31).
            reference (date | None): The reference date to calculate the next billing date. Defaults to today if None.

        Returns:
        -----
            date: The next billing date based on the billing day and reference date.
        """
        BillingService.validate_billing_day(billing_day)
        if reference is None:
            reference = timezone.now().date()

        # Start with candidate in this month, clamped if needed
        this_month_candidate = reference + relativedelta(day=billing_day)

        if this_month_candidate > reference:
            return this_month_candidate
        else:
            # Advance one month, same day (clamped to EOM if needed)
            return reference + relativedelta(months=+1, day=billing_day)

    @staticmethod
    @tracer.start_as_current_span("BillingService get_current_billing_period_from_day")
    def get_current_billing_period_from_day(billing_day: int, today: date | None = None) -> tuple[date, date]:
        """
        Return (start, end) of the current billing period, given a billing day-of-month (1–31).

        The period is defined as:
        - Start: The billing day of the month, adjusted to the current month or previous month if today is past the billing day.
        - End: The day before the next billing period starts.

        Args:
        -----
            billing_day (int): The day of the month when billing occurs (1–31).
            today (date | None): The reference date to calculate the billing period. Defaults to today if None.

        Returns:
        -----
            tuple[date, date]: A tuple containing the start and end dates of the current billing period.
        """
        BillingService.validate_billing_day(billing_day)
        if today is None:
            today = timezone.now().date()

        # Candidate billing start for this month
        this_month_start = today + relativedelta(day=billing_day)
        if this_month_start <= today:
            period_start = this_month_start
        else:
            # Go back to previous month
            period_start = (today - relativedelta(months=1)) + relativedelta(day=billing_day)

        # Period end = day before next period start
        next_period_start = period_start + relativedelta(months=1, day=billing_day)
        period_end = next_period_start - relativedelta(days=1)

        return period_start, period_end

    @staticmethod
    @tracer.start_as_current_span("BillingService get_current_billing_period_for_user")
    def get_current_billing_period_for_user(user) -> tuple[date, date]:
        """Backward-compatible wrapper around owner-aware billing period lookup."""
        return BillingService.get_current_billing_period_for_owner(user)

    @staticmethod
    @tracer.start_as_current_span("BillingService get_current_billing_period_for_owner")
    def get_current_billing_period_for_owner(owner) -> tuple[date, date]:
        """Return (start, end) for the billing period tied to a user or organization owner."""
        span = trace.get_current_span()
        today = timezone.now().date()

        record, owner_type = BillingService._get_billing_record_for_owner(owner)

        if record is not None:
            billing_day = record.billing_cycle_anchor
            span.set_attribute("billing.owner_type", owner_type)
            span.set_attribute("billing.owner_id", getattr(owner, "id", None))
            return BillingService.get_current_billing_period_from_day(billing_day, today)

        span.add_event("Billing record not found; defaulting to day 1", {"owner_type": owner_type})
        logger.warning(
            "Billing record not found for owner_id=%s owner_type=%s; using default billing day 1.",
            getattr(owner, "id", None),
            owner_type,
        )

        return BillingService.get_current_billing_period_from_day(1, today)
