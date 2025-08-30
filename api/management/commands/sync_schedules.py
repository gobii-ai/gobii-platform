from django.core.management.base import BaseCommand
from api.periodic_tasks import sync_to_redis

class Command(BaseCommand):
    help = "Sync all periodic task schedules from code into Redis (RedBeat)."

    def handle(self, *args, **opts):
        sync_to_redis()
        self.stdout.write(self.style.SUCCESS("✅ schedules synced to Redis"))