# Generated migration for cleaning up django-celery-beat tables

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0019_add_paid_plan_intent'),
    ]

    operations = [
        # Drop django-celery-beat tables if they exist
        # These tables are no longer needed after migrating to RedBeat
        migrations.RunSQL(
            sql="""
            DROP TABLE IF EXISTS django_celery_beat_periodictask CASCADE;
            DROP TABLE IF EXISTS django_celery_beat_intervalschedule CASCADE;
            DROP TABLE IF EXISTS django_celery_beat_crontabschedule CASCADE;
            DROP TABLE IF EXISTS django_celery_beat_solarschedule CASCADE;
            DROP TABLE IF EXISTS django_celery_beat_clockedschedule CASCADE;
            DROP TABLE IF EXISTS django_celery_beat_periodictasks CASCADE;
            """,
            reverse_sql="""
            -- This migration cannot be reversed as the table schemas are not preserved
            -- If you need to restore django-celery-beat, you'll need to:
            -- 1. Add django-celery-beat back to INSTALLED_APPS
            -- 2. Run migrations to recreate the tables
            -- 3. Restore any schedule data from backup if needed
            SELECT 1; -- No-op for reverse migration
            """,
        ),
    ]