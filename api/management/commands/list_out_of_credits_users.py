from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db.models import Sum, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone


class Command(BaseCommand):
    help = "List users who are currently out of credits (sum of available credits in current range is 0)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            action="store_true",
            default=False,
            help="Output as CSV with headers: user_id,email,available_credits",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        now = timezone.now()

        users = (
            User.objects.filter(is_active=True)
            .annotate(
                available_credits_sum=Coalesce(
                    Sum(
                        "task_credits__available_credits",
                        filter=Q(
                            task_credits__granted_date__lte=now,
                            task_credits__expiration_date__gte=now,
                            task_credits__voided=False,
                            task_credits__user__isnull=False,  # user-owned credits only
                        ),
                    ),
                    Value(0),
                )
            )
            .filter(available_credits_sum__lte=0)
            .order_by("email", "id")
            .values("id", "email", "available_credits_sum")
        )

        as_csv = bool(options.get("csv"))
        if as_csv:
            self.stdout.write("user_id,email,available_credits")
            for u in users:
                self.stdout.write(
                    f"{u['id']},{u['email'] or ''},{_fmt_dec(u['available_credits_sum'])}"
                )
        else:
            count = 0
            for u in users:
                count += 1
                self.stdout.write(
                    f"{u['id']}\t{u['email'] or ''}\tavailable={_fmt_dec(u['available_credits_sum'])}"
                )
            self.stdout.write(self.style.SUCCESS(f"Total users out of credits: {count}"))


def _fmt_dec(val) -> str:
    try:
        if isinstance(val, Decimal):
            return f"{val:.3f}"
        return str(val)
    except Exception:
        return str(val)

