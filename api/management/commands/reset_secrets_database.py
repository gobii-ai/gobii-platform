"""
Management command to reset the database for secrets migration while preserving user data.
This is specifically for the transition to name/description-based secrets.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
import json
import os


class Command(BaseCommand):
    help = 'Reset database for secrets migration while preserving user and core settings'

    def add_arguments(self, parser):
        parser.add_argument(
            '--backup-file',
            type=str,
            default='user_backup.json',
            help='File to store user backup data'
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirm that you want to reset the database'
        )

    def handle(self, *args, **options):
        backup_file = options['backup_file']
        
        if not options['confirm']:
            self.stdout.write(
                self.style.WARNING(
                    'This command will reset most of the database while preserving users.\n'
                    'Use --confirm to proceed.\n'
                    'Usage: python manage.py reset_secrets_database --confirm'
                )
            )
            return

        try:
            with transaction.atomic():
                self.stdout.write('Starting database reset...')
                
                # 1. Backup user data
                self.stdout.write('Backing up user data...')
                self._backup_users(backup_file)
                
                # 2. Clear secrets and related data
                self.stdout.write('Clearing old secrets data...')
                self._clear_secrets_data()
                
                # 3. Clear agents but preserve user relationships
                self.stdout.write('Clearing persistent agents...')
                self._clear_agents()
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Database reset complete!\n'
                        f'User data backed up to: {backup_file}\n'
                        f'You can now run migrations: python manage.py migrate'
                    )
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error during database reset: {str(e)}')
            )
            raise

    def _backup_users(self, backup_file):
        """Backup user data to a JSON file."""
        User = get_user_model()
        
        users_data = []
        for user in User.objects.all():
            user_data = {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_staff': user.is_staff,
                'is_superuser': user.is_superuser,
                'is_active': user.is_active,
                'date_joined': user.date_joined.isoformat(),
            }
            users_data.append(user_data)
        
        with open(backup_file, 'w') as f:
            json.dump(users_data, f, indent=2)
        
        self.stdout.write(f'  Backed up {len(users_data)} users to {backup_file}')

    def _clear_secrets_data(self):
        """Clear all secrets-related data."""
        from api.models import PersistentAgentSecret
        
        # Clear new secrets model if it exists
        try:
            count = PersistentAgentSecret.objects.count()
            PersistentAgentSecret.objects.all().delete()
            self.stdout.write(f'  Cleared {count} secrets from PersistentAgentSecret')
        except Exception as e:
            self.stdout.write(f'  PersistentAgentSecret not found or error: {e}')

    def _clear_agents(self):
        """Clear both persistent agents and browser use agents."""
        from api.models import PersistentAgent, BrowserUseAgent
        
        # Clear PersistentAgent objects
        try:
            pa_count = PersistentAgent.objects.count()
            # Get user relationships before deletion
            user_agents = list(PersistentAgent.objects.values('user__email', 'name'))
            
            PersistentAgent.objects.all().delete()
            
            self.stdout.write(f'  Cleared {pa_count} persistent agents')
            if user_agents:
                self.stdout.write('  User-agent relationships that were cleared:')
                for rel in user_agents[:10]:  # Show first 10
                    self.stdout.write(f'    {rel["user__email"]}: {rel["name"]}')
                if len(user_agents) > 10:
                    self.stdout.write(f'    ... and {len(user_agents) - 10} more')
                    
        except Exception as e:
            self.stdout.write(f'  PersistentAgent not found or error: {e}')

        # Clear BrowserUseAgent objects (one-time task agents)
        try:
            bua_count = BrowserUseAgent.objects.count()
            # Get user relationships before deletion
            browser_agents = list(BrowserUseAgent.objects.values('user__email', 'name'))
            
            BrowserUseAgent.objects.all().delete()
            
            self.stdout.write(f'  Cleared {bua_count} browser use agents')
            if browser_agents:
                self.stdout.write('  Browser agent relationships that were cleared:')
                for rel in browser_agents[:10]:  # Show first 10
                    self.stdout.write(f'    {rel["user__email"]}: {rel["name"]}')
                if len(browser_agents) > 10:
                    self.stdout.write(f'    ... and {len(browser_agents) - 10} more')
                    
        except Exception as e:
            self.stdout.write(f'  BrowserUseAgent not found or error: {e}') 