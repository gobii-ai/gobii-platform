from django.db import migrations, models
import django.utils.timezone

class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='LandingPage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(blank=True, max_length=128, unique=True)),
                ('charter', models.TextField()),
                ('title', models.CharField(blank=True, max_length=512)),
                ('hero_text', models.CharField(blank=True, max_length=256)),
                ('image_url', models.URLField(blank=True)),
                ('hits', models.PositiveIntegerField(default=0)),
                ('disabled', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
