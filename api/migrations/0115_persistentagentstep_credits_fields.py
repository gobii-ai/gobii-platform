from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0114_browseruseagenttask_credits_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentstep",
            name="task_credit",
            field=models.ForeignKey(
                to="api.taskcredit",
                on_delete=models.SET_NULL,
                null=True,
                blank=True,
                related_name="agent_steps",
            ),
        ),
        migrations.AddField(
            model_name="persistentagentstep",
            name="credits_cost",
            field=models.DecimalField(
                max_digits=12,
                decimal_places=3,
                null=True,
                blank=True,
                help_text="Credits charged for this step; defaults to configured per‑task cost.",
            ),
        ),
    ]

