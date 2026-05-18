from django.db import migrations, models


SWITCH_NAME = "sms_contact_purpose_required"
SMS_CONTACT_PURPOSE_CHOICES = [
    ("team_operational", "Team operational"),
    ("owner_delegate", "Owner delegate"),
    ("customer_care", "Customer care"),
    ("other_operational", "Other operational"),
]
SMS_CONTACT_PURPOSE_HELP = (
    "Operational purpose for SMS contacts. Null for legacy rows and non-SMS contacts."
)
SMS_CONTACT_PURPOSE_DETAILS_HELP = "Optional additional context for the SMS contact purpose."
SMS_CONTACT_PERMISSION_ATTESTED_HELP = (
    "Whether the approver confirmed permission to contact this number by SMS."
)
SMS_CONTACT_PERMISSION_ATTESTED_AT_HELP = "When SMS contact permission was attested."


def add_switch(apps, schema_editor):
    """Create the SMS contact purpose rollout switch disabled by default."""
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return

    Switch.objects.update_or_create(
        name=SWITCH_NAME,
        defaults={"active": False},
    )


def remove_switch(apps, schema_editor):
    try:
        Switch = apps.get_model("waffle", "Switch")
    except LookupError:
        return

    Switch.objects.filter(name=SWITCH_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0382_persistentagentdiscordwebhookecho"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentallowlistinvite",
            name="sms_contact_purpose",
            field=models.CharField(
                blank=True,
                choices=SMS_CONTACT_PURPOSE_CHOICES,
                help_text=SMS_CONTACT_PURPOSE_HELP,
                max_length=32,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="agentallowlistinvite",
            name="sms_contact_purpose_details",
            field=models.TextField(
                blank=True,
                help_text=SMS_CONTACT_PURPOSE_DETAILS_HELP,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="agentallowlistinvite",
            name="sms_contact_permission_attested",
            field=models.BooleanField(
                blank=True,
                help_text=SMS_CONTACT_PERMISSION_ATTESTED_HELP,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="agentallowlistinvite",
            name="sms_contact_permission_attested_at",
            field=models.DateTimeField(
                blank=True,
                help_text=SMS_CONTACT_PERMISSION_ATTESTED_AT_HELP,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="commsallowlistentry",
            name="sms_contact_purpose",
            field=models.CharField(
                blank=True,
                choices=SMS_CONTACT_PURPOSE_CHOICES,
                help_text=SMS_CONTACT_PURPOSE_HELP,
                max_length=32,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="commsallowlistentry",
            name="sms_contact_purpose_details",
            field=models.TextField(
                blank=True,
                help_text=SMS_CONTACT_PURPOSE_DETAILS_HELP,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="commsallowlistentry",
            name="sms_contact_permission_attested",
            field=models.BooleanField(
                blank=True,
                help_text=SMS_CONTACT_PERMISSION_ATTESTED_HELP,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="commsallowlistentry",
            name="sms_contact_permission_attested_at",
            field=models.DateTimeField(
                blank=True,
                help_text=SMS_CONTACT_PERMISSION_ATTESTED_AT_HELP,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="commsallowlistrequest",
            name="sms_contact_purpose",
            field=models.CharField(
                blank=True,
                choices=SMS_CONTACT_PURPOSE_CHOICES,
                help_text=SMS_CONTACT_PURPOSE_HELP,
                max_length=32,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="commsallowlistrequest",
            name="sms_contact_purpose_details",
            field=models.TextField(
                blank=True,
                help_text=SMS_CONTACT_PURPOSE_DETAILS_HELP,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="commsallowlistrequest",
            name="sms_contact_permission_attested",
            field=models.BooleanField(
                blank=True,
                help_text=SMS_CONTACT_PERMISSION_ATTESTED_HELP,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="commsallowlistrequest",
            name="sms_contact_permission_attested_at",
            field=models.DateTimeField(
                blank=True,
                help_text=SMS_CONTACT_PERMISSION_ATTESTED_AT_HELP,
                null=True,
            ),
        ),
        migrations.RunPython(add_switch, remove_switch),
    ]
