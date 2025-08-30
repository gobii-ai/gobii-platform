from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp


class Command(BaseCommand):
    help = 'Sets up initial site configuration and Google OAuth placeholder for development'

    def handle(self, *args, **options):
        try:
            site = Site.objects.get_current()
            self.stdout.write(f'Using current site: {site.domain}')
            
            app, created = SocialApp.objects.get_or_create(
                provider='google',
                name='Google OAuth (placeholder)',
                defaults={
                    'client_id': 'x',
                    'secret': 'x'
                }
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS('Created Google OAuth placeholder app'))
            else:
                self.stdout.write('Google OAuth app already exists')
            
            if not app.sites.filter(pk=site.pk).exists():
                app.sites.add(site)
                app.save()
                self.stdout.write(self.style.SUCCESS(f'Added site {site.domain} to Google OAuth app'))
            else:
                self.stdout.write(f'Site {site.domain} already associated with Google OAuth app')
            
            self.stdout.write(self.style.SUCCESS('Initial site setup complete!'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error during setup: {e}'))
            raise