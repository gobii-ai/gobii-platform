from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from api.models import Organization
from api.services.owner_execution_pause import (
    get_owner_execution_pause_state,
    pause_owner_execution,
    resume_owner_execution,
)
from util.analytics import AnalyticsSource


class Command(BaseCommand):
    help = "Show, pause, or resume owner execution for a user or organization."

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=["show", "pause", "resume"],
            help="Whether to show the current state, pause execution, or resume execution.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="Target a user owner by numeric id.",
        )
        parser.add_argument(
            "--user-email",
            help="Target a user owner by email.",
        )
        parser.add_argument(
            "--org-id",
            type=int,
            help="Target an organization owner by numeric id.",
        )
        parser.add_argument(
            "--org-slug",
            help="Target an organization owner by slug.",
        )
        parser.add_argument(
            "--reason",
            default="billing_delinquency",
            help="Machine-readable pause reason to store when pausing.",
        )
        parser.add_argument(
            "--source",
            default="management_command.owner_execution_pause",
            help="Source string stored in logs for pause or resume actions.",
        )
        parser.add_argument(
            "--skip-cleanup",
            action="store_true",
            help="Pause without shutting down currently active agents.",
        )
        parser.add_argument(
            "--skip-enqueue",
            action="store_true",
            help="Resume without enqueueing active agents back into processing.",
        )

    def handle(self, *args, **options):
        owner = self._resolve_owner(options)
        action = options["action"]

        if action == "pause":
            pause_owner_execution(
                owner,
                options["reason"],
                source=options["source"],
                trigger_agent_cleanup=not options["skip_cleanup"],
                analytics_source=AnalyticsSource.NA,
            )
        elif action == "resume":
            resume_owner_execution(
                owner,
                source=options["source"],
                enqueue_agent_resume=not options["skip_enqueue"],
            )

        state = get_owner_execution_pause_state(owner)
        self.stdout.write(
            self.style.SUCCESS(
                "owner=%s paused=%s reason=%s paused_at=%s"
                % (
                    self._format_owner(owner),
                    state["paused"],
                    state["reason"] or "-",
                    state["paused_at"].isoformat() if state["paused_at"] else "-",
                )
            )
        )

    def _resolve_owner(self, options):
        selectors = [
            options.get("user_id") is not None,
            bool(options.get("user_email")),
            options.get("org_id") is not None,
            bool(options.get("org_slug")),
        ]
        if sum(selectors) != 1:
            raise CommandError("Provide exactly one owner selector: --user-id, --user-email, --org-id, or --org-slug.")

        User = get_user_model()
        if options.get("user_id") is not None:
            owner = User.objects.filter(pk=options["user_id"]).first()
            if owner is None:
                raise CommandError(f"User not found for id={options['user_id']}.")
            return owner

        if options.get("user_email"):
            owner = User.objects.filter(email=options["user_email"]).first()
            if owner is None:
                raise CommandError(f"User not found for email={options['user_email']}.")
            return owner

        if options.get("org_id") is not None:
            owner = Organization.objects.filter(pk=options["org_id"]).first()
            if owner is None:
                raise CommandError(f"Organization not found for id={options['org_id']}.")
            return owner

        owner = Organization.objects.filter(slug=options["org_slug"]).first()
        if owner is None:
            raise CommandError(f"Organization not found for slug={options['org_slug']}.")
        return owner

    def _format_owner(self, owner):
        if isinstance(owner, Organization):
            return f"organization:{owner.id}:{owner.slug}"
        return f"user:{owner.id}:{owner.email}"
