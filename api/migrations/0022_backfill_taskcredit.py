from datetime import timedelta

from django.utils import timezone
from django.db import migrations
from django.contrib.auth import get_user_model

from config.settings import INITIAL_TASK_CREDIT_EXPIRATION_DAYS
from util.subscription_helper import get_user_task_credit_limit


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    TaskCredit = apps.get_model("api", "TaskCredit")
    RealUser = get_user_model()

    now = timezone.now()
    expires = now + timedelta(days=INITIAL_TASK_CREDIT_EXPIRATION_DAYS)

    for user in User.objects.all():
        if TaskCredit.objects.filter(user_id=user.id).exists():
            continue
        runtime_user = RealUser.objects.get(pk=user.id)
        credit_amount = get_user_task_credit_limit(runtime_user)
        TaskCredit.objects.create(
            user_id=user.id,
            credits=credit_amount,
            granted_date=now,
            expiration_date=expires,
        )

def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0021_task_credit_system'),
        ("djstripe", "0014_2_9a"),
    ]

    operations = [
        migrations.RunPython(forwards, noop),
    ]
