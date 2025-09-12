from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0121_llm_config_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='persistentmodelendpoint',
            name='api_base',
            field=models.CharField(blank=True, max_length=256),
        ),
    ]

