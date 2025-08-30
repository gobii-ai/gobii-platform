from django.db import migrations, models
import django.db.models.deletion
import uuid
from django.conf import settings

class Migration(migrations.Migration):
    dependencies = [
        ('api', '0019_add_paid_plan_intent'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TaskCredit',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('credits', models.PositiveIntegerField()),
                ('credits_used', models.PositiveIntegerField(default=0)),
                ('granted_date', models.DateTimeField()),
                ('expiration_date', models.DateTimeField()),
                ('stripe_invoice_id', models.CharField(max_length=128, null=True, blank=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='task_credits', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-granted_date'],
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'stripe_invoice_id'), name='unique_task_credit_invoice', condition=models.Q(stripe_invoice_id__isnull=False)),
                ],
            },
        ),
        migrations.AddField(
            model_name='browseruseagenttask',
            name='task_credit',
            field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to='api.taskcredit'),
        ),
    ]
