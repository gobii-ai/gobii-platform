from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0203_eval_suite_run_requested_runs"),
    ]

    operations = [
        migrations.AddField(
            model_name="browseruseagenttask",
            name="eval_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="browser_tasks",
                to="api.evalrun",
                help_text="Eval run that spawned this browser task, if any.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagentcompletion",
            name="eval_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="agent_completions",
                to="api.evalrun",
                help_text="Eval run context for this completion, when applicable.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagentstep",
            name="eval_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="agent_steps",
                to="api.evalrun",
                help_text="Eval run context for this step, when applicable.",
            ),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="prompt_tokens",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="completion_tokens",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="cached_tokens",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="input_cost_total",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="input_cost_uncached",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="input_cost_cached",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="output_cost",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalrun",
            name="total_cost",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="prompt_tokens",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="completion_tokens",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="total_tokens",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="cached_tokens",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="input_cost_total",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="input_cost_uncached",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="input_cost_cached",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="output_cost",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="total_cost",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=12),
        ),
        migrations.AddField(
            model_name="evalruntask",
            name="credits_cost",
            field=models.DecimalField(decimal_places=6, default=Decimal("0"), max_digits=20),
        ),
    ]
