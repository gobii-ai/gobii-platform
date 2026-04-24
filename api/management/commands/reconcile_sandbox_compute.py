import json

from django.core.management.base import BaseCommand

from api.services.sandbox_compute_lifecycle import SandboxComputeScheduler


class Command(BaseCommand):
    help = "Reconcile terminal sandbox compute sessions and delete their Kubernetes resources."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--grace-seconds", type=int, default=900)
        parser.add_argument("--delete-workspaces", action="store_true")
        parser.add_argument("--delete-snapshots", action="store_true")
        parser.add_argument("--force-delete-workspaces", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        scheduler = SandboxComputeScheduler()
        result = scheduler.reconcile_terminal_sessions(
            limit=options["limit"],
            grace_seconds=options["grace_seconds"],
            delete_workspaces=options["delete_workspaces"],
            delete_snapshots=options["delete_snapshots"],
            force_delete_workspaces=options["force_delete_workspaces"],
            dry_run=options["dry_run"],
        )
        self.stdout.write(json.dumps(result, indent=2, default=str))
