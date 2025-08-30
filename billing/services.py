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
        """
        Get the current billing period based on the billing day set in the service.

        This method assumes a default billing day of 1 if not set.

        Args:
        -----
            user: The user object for whom the billing period is being calculated.

        Returns:
        -----
            tuple[date, date]: A tuple containing the start and end dates of the current billing period.
        """
        span = trace.get_current_span()
        today = timezone.now().date()

        UserBilling = apps.get_model("api", "UserBilling")

        # Assuming the UserBilling model has a foreign key to User and a billing_day field
        user_billing = UserBilling.objects.filter(user_id=user.id).first()

        if user_billing is not None:
            billing_day = user_billing.billing_cycle_anchor
            return BillingService.get_current_billing_period_from_day(billing_day, today)

        logger.warning(f"UserBilling not found for user_id: {user.id}; using default billing day 1.")
        span.add_event("UserBilling not found, using default billing day 1")

        return BillingService.get_current_billing_period_from_day(1, today)
