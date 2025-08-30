"""
Management command to setup periodic Decodo IP block sync.
"""
import os
from celery.schedules import crontab
from django.core.management.base import BaseCommand
from django.conf import settings
from redbeat import RedBeatScheduler
from config.redis_client import get_redis_client


class Command(BaseCommand):
    help = 'Setup or remove the Decodo IP sync task in Redis Beat'

    def add_arguments(self, parser):
        parser.add_argument(
            '--remove',
            action='store_true',
            help='Remove the sync task instead of adding it',
        )

    def handle(self, *args, **options):
        """Setup or remove the periodic sync task."""
        try:
            # Get Redis client
            redis_client = get_redis_client()
            
            # Create scheduler
            scheduler = RedBeatScheduler(app=None, redis=redis_client)
            
            task_name = 'decodo-ip-sync-daily'
            
            if options['remove']:
                # Remove the task
                try:
                    scheduler.delete(task_name)
                    self.stdout.write(
                        self.style.SUCCESS(f'Successfully removed task: {task_name}')
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(f'Task {task_name} not found or error removing: {e}')
                    )
            else:
                # Add/update the task
                schedule_entry = scheduler.Entry(
                    name=task_name,
                    task='api.tasks.sync_decodo_ip_blocks',
                    schedule=crontab(hour=6, minute=0),  # Daily at 6 AM UTC
                    enabled=True
                )
                
                # Save the entry
                schedule_entry.save()
                
                self.stdout.write(
                    self.style.SUCCESS(f'Successfully setup task: {task_name}')
                )
                self.stdout.write(f'Schedule: {schedule_entry.schedule}')
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error setting up Redis Beat scheduler: {e}')
            )
            raise