from django.core.management.base import BaseCommand
from api.tasks.sms_tasks import sync_twilio_numbers

class Command(BaseCommand):
    help = "One-shot sync of Twilio phone-number pool."

    def handle(self, *args, **options):
        result = sync_twilio_numbers.apply()  # runs synchronously
        self.stdout.write(self.style.SUCCESS("Sync complete"))