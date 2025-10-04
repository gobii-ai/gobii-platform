"""
Django management command: buy_numbers
=====================================
Bulk‑purchase Twilio phone numbers that match specific criteria and attach them
immediately to a Messaging Service.

The command is a direct port of the original `buy_numbers.py` script but wrapped
in Django’s `BaseCommand` framework so it can be invoked via the standard
`python manage.py …` interface.  Behaviour and CLI flags remain identical.

Example usages
--------------
# Dry‑run – preview five US numbers ending in GOBII without purchasing
$ python manage.py buy_numbers --count 5 --country US \
                         --vanity GOBII \
                         --dry-run

# Buy ten Virginia (571) numbers and add them to a Messaging Service
$ python manage.py buy_numbers --count 10 --country US --area-code 571
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from config.settings import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_MESSAGING_SERVICE_SID
from util.integrations import twilio_status
from util import sms

try:
    from twilio.base.exceptions import TwilioRestException
    from twilio.rest import Client  # type: ignore
except ImportError as exc:  # pragma: no cover – optional dependency
    raise CommandError(
        "Twilio SDK missing – install with `pip install twilio`.") from exc


class Command(BaseCommand):
    help = (
        "Bulk‑purchase Twilio phone numbers that match specific criteria and "
        "attach them to the supplied Messaging Service."
    )

    def add_arguments(self, parser):
        """
        Add command line arguments for the buy_numbers command.
        This method defines the options that can be passed to the command
        when it is executed from the command line.

        Arguments:
        --count: Number of phone numbers to purchase (required).
        --country: ISO‑3166 country code (default: US).
        --area-code: Restrict to a specific area code (US/CA only).
        --sms-only: Only consider SMS‑enabled numbers.
        --vanity: Vanity word/digits the number must end with (case-insensitive).
        --dry-run: List matching numbers but do not purchase them.
        """
        parser.add_argument(
            "--count",
            type=int,
            required=True,
            help="How many numbers to buy",
        )
        parser.add_argument(
            "--country",
            default="US",
            help="ISO‑3166 country code (default: US)",
        )
        parser.add_argument(
            "--area-code",
            help="Restrict to a specific area code (US/CA only)",
        )
        parser.add_argument(
            "--sms-only",
            action="store_true",
            help="Only consider SMS‑enabled numbers",
        )
        parser.add_argument(
            "--vanity",
            help=(
                "Vanity word/digits the number *must end with* – e.g. GOBII. "
                "Case‑insensitive."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List matching numbers but *do not* purchase",
        )

    # ------------------------------------------------------------------
    # Main entrypoint
    # ------------------------------------------------------------------
    def handle(self, *args, **opts):  # noqa: PLR0912 – single main function OK
        # ── Twilio credentials ───────────────────────────────────────────
        status = twilio_status()
        if not status.enabled:
            raise CommandError(f"Twilio integration disabled: {status.reason or 'no reason provided'}")

        acct_sid = TWILIO_ACCOUNT_SID
        auth_tok = TWILIO_AUTH_TOKEN

        if not (acct_sid and auth_tok):
            raise CommandError(
                "Twilio credentials missing – set TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN "
                "in environment variables or Django settings."
            )

        client = Client(acct_sid, auth_tok)

        candidates = sms.sms_twilio_find_numbers(
            country=opts["country"],
            area_code=opts.get("area_code"),
            vanity=opts.get("vanity"),
            count=opts["count"],
            sms_only=opts["sms_only"],
        )

        if not candidates:
            raise CommandError("No phone numbers matched your criteria.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Found {len(candidates)} matching numbers "
                f"({ 'dry‑run' if opts['dry_run'] else 'will purchase' })"
            )
        )

        # ── Purchase & attach ───────────────────────────────────────────
        purchased = 0
        dry_run = opts["dry_run"]
        desired = opts["count"]
        service_sid = TWILIO_MESSAGING_SERVICE_SID

        for num in candidates:
            if purchased >= desired:
                break

            if dry_run:
                self.stdout.write(f"  {num.phone_number}")
                purchased += 1
                continue

            bought_num = sms.sms_twilio_purchase_numbers(num.phone_number)

            if not bought_num:
                self.stderr.write(
                    self.style.ERROR(f"Failed to purchase {num.phone_number}")
                )
                continue
            else:
                purchased += 1
                self.stdout.write(
                    self.style.SUCCESS(f"✔︎ Bought {num.phone_number}")
                )

        # ── Summary ────────────────────────────────────────────────────
        if dry_run:
            self.stdout.write(
                f"\nSummary: {purchased} number(s) matched (dry‑run) and would be "
                f"added to {service_sid}."
            )
        else:
            self.stdout.write(
                f"\nSummary: {purchased} number(s) purchased and added to "
                f"{service_sid}."
            )
            if purchased < desired:
                self.stderr.write(
                    self.style.WARNING(
                        "Desired count not reached – see messages above."
                    )
                )
