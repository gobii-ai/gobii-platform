from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the soft-expiration sweeper for inactive free-plan agents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--async",
            dest="enqueue",
            action="store_true",
            help="Enqueue the Celery task instead of running synchronously.",
        )

    def handle(self, *args, **opts):
        enqueue: bool = bool(opts.get("enqueue"))

        # Import here so Django is fully initialized
        from api.tasks.soft_expiration_task import soft_expire_inactive_agents_task

        if enqueue:
            result = soft_expire_inactive_agents_task.delay()
            self.stdout.write(self.style.SUCCESS(f"✅ enqueued soft-expiration task (task_id={result.id})"))
            return

        # Run synchronously in-process
        expired = soft_expire_inactive_agents_task.apply().get()
        self.stdout.write(self.style.SUCCESS(f"✅ soft-expiration completed; expired {expired} agents"))

