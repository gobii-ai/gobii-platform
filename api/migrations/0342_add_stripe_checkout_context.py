from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0341_merge_20260408_1608"),
    ]

    operations = [
        migrations.CreateModel(
            name="StripeCheckoutContext",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("stripe_customer_id", models.CharField(max_length=255, db_index=True)),
                ("stripe_checkout_session_id", models.CharField(max_length=255, unique=True)),
                ("stripe_setup_intent_id", models.CharField(max_length=255, null=True, blank=True, unique=True)),
                ("event_id", models.CharField(max_length=255, db_index=True)),
                ("flow_type", models.CharField(max_length=32, db_index=True)),
                ("plan", models.CharField(max_length=64, blank=True, default="")),
                ("plan_label", models.CharField(max_length=64, blank=True, default="")),
                ("value", models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)),
                ("currency", models.CharField(max_length=16, blank=True, default="")),
                ("checkout_source_url", models.CharField(max_length=500, blank=True, default="")),
                ("stripe_session_created_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Stripe checkout context",
                "verbose_name_plural": "Stripe checkout contexts",
            },
        ),
        migrations.AddIndex(
            model_name="stripecheckoutcontext",
            index=models.Index(
                fields=("stripe_customer_id", "flow_type"),
                name="stripe_chk_ctx_cust_flow_idx",
            ),
        ),
    ]
