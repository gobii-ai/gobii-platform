# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0089_add_parent_only_index'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='agentfilespaceaccess',
            constraint=models.UniqueConstraint(
                fields=('agent',),
                condition=models.Q(is_default=True),
                name='unique_default_filespace_per_agent',
            ),
        ),
    ]
