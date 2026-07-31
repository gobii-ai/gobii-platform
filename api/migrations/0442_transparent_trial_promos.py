import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0441_disable_unseen_web_chat_followups"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="is_listed",
            field=models.BooleanField(
                default=True,
                help_text="Whether this template appears on public template surfaces and ordinary launch routes.",
            ),
        ),
        migrations.AddField(
            model_name="trialpromo",
            name="activation_mode",
            field=models.CharField(
                choices=[
                    ("hosted_checkout", "Hosted Stripe Checkout"),
                    ("direct_stripe_trial", "Transparent Stripe trial"),
                ],
                default="hosted_checkout",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="trialpromo",
            name="conversion_coupon_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Stripe repeating coupon applied only after the free trial.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="trialpromo",
            name="discount_months",
            field=models.PositiveIntegerField(
                default=3,
                help_text="Number of paid monthly billing periods that receive the conversion discount.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(24),
                ],
            ),
        ),
        migrations.AddField(
            model_name="trialpromo",
            name="late_conversion_grace_days",
            field=models.PositiveIntegerField(
                default=30,
                help_text="Late-conversion window after trial end when the promo has no active-until date.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(365),
                ],
            ),
        ),
        migrations.AddField(
            model_name="trialpromo",
            name="linked_template",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional unlisted personal template staged after transparent trial activation.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="trial_promos",
                to="api.persistentagenttemplate",
            ),
        ),
        migrations.AddField(
            model_name="trialpromoredemption",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trialpromoredemption",
            name="conversion_checkout_session_id",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="trialpromoredemption",
            name="discount_applied_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trialpromoredemption",
            name="discount_state",
            field=models.CharField(
                choices=[
                    ("not_applicable", "Not applicable"),
                    ("available", "Available after trial"),
                    ("redeemed", "Discount redeemed"),
                ],
                default="not_applicable",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="trialpromoredemption",
            name="late_conversion_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trialpromoredemption",
            name="stripe_subscription_schedule_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="trialpromoredemption",
            name="status",
            field=models.CharField(
                choices=[
                    ("checkout_started", "Checkout started"),
                    ("checkout_completed", "Checkout completed"),
                    ("checkout_expired", "Checkout expired"),
                    ("checkout_failed", "Checkout failed"),
                    ("direct_activation_pending", "Direct activation pending"),
                    ("direct_activation_completed", "Direct activation completed"),
                    ("direct_activation_failed", "Direct activation failed"),
                ],
                db_index=True,
                default="checkout_started",
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="trialpromoredemption",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    status__in=(
                        "direct_activation_pending",
                        "direct_activation_completed",
                    ),
                ),
                fields=("promo", "user"),
                name="uniq_active_direct_trial_promo_redemption",
            ),
        ),
    ]
