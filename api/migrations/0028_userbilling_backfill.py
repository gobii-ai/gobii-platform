from django.db import migrations
from django.conf import settings
from constants.plans import PlanNames

def create_user_billing_records(apps, schema_editor):
    """
    Create UserBilling records for all existing users.
    Set subscription to PlanNames.FREE and max_extra_tasks to 0.
    """
    User = apps.get_model(settings.AUTH_USER_MODEL)
    UserBilling = apps.get_model('api', 'UserBilling')
    
    # Get all users that don't have a UserBilling record yet
    for user in User.objects.all():
        # Use get_or_create to avoid duplicates in case some users already have records
        UserBilling.objects.get_or_create(
            user_id=user.id,
            defaults={
                'subscription': PlanNames.FREE,
                'max_extra_tasks': 0
            }
        )

def reverse_user_billing_creation(apps, schema_editor):
    """
    Do nothing on reverse migration.
    We don't want to delete billing records if the migration is reversed.
    """
    pass

class Migration(migrations.Migration):

    dependencies = [
        # Replace this with the actual last migration of your app
        ('api', '0027_userbilling'),
    ]

    operations = [
        migrations.RunPython(
            create_user_billing_records,
            reverse_user_billing_creation
        ),
    ]