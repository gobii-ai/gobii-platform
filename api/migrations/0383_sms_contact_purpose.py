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
SMS_INVITE_CONTACT_PERMISSION_ATTESTED_HELP = (
    "Whether the inviter confirmed permission to contact this number by SMS."
)
SMS_CONTACT_PERMISSION_ATTESTED_AT_HELP = "When SMS contact permission was attested."


def _add_nullable_field_if_missing(model_name, table_name, field_name, sql_type, field):
    return migrations.SeparateDatabaseAndState(
        database_operations=[
            migrations.RunSQL(
                sql=f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{field_name}" {sql_type} NULL;',
                reverse_sql=f'ALTER TABLE "{table_name}" DROP COLUMN IF EXISTS "{field_name}";',
            )
        ],
        state_operations=[
            migrations.AddField(
                model_name=model_name,
                name=field_name,
                field=field,
            )
        ],
    )


def _sms_contact_purpose_field():
    return models.CharField(
        blank=True,
        choices=SMS_CONTACT_PURPOSE_CHOICES,
        help_text=SMS_CONTACT_PURPOSE_HELP,
        max_length=32,
        null=True,
    )


def _sms_contact_purpose_details_field():
    return models.TextField(
        blank=True,
        help_text=SMS_CONTACT_PURPOSE_DETAILS_HELP,
        null=True,
    )


def _sms_contact_permission_attested_field(help_text=SMS_CONTACT_PERMISSION_ATTESTED_HELP):
    return models.BooleanField(
        blank=True,
        help_text=help_text,
        null=True,
    )


def _sms_contact_permission_attested_at_field():
    return models.DateTimeField(
        blank=True,
        help_text=SMS_CONTACT_PERMISSION_ATTESTED_AT_HELP,
        null=True,
    )


def _sms_contact_metadata_operations(
    model_name,
    table_name,
    *,
    permission_attested_help_text=SMS_CONTACT_PERMISSION_ATTESTED_HELP,
):
    return [
        _add_nullable_field_if_missing(
            model_name,
            table_name,
            "sms_contact_purpose",
            "varchar(32)",
            _sms_contact_purpose_field(),
        ),
        _add_nullable_field_if_missing(
            model_name,
            table_name,
            "sms_contact_purpose_details",
            "text",
            _sms_contact_purpose_details_field(),
        ),
        _add_nullable_field_if_missing(
            model_name,
            table_name,
            "sms_contact_permission_attested",
            "boolean",
            _sms_contact_permission_attested_field(permission_attested_help_text),
        ),
        _add_nullable_field_if_missing(
            model_name,
            table_name,
            "sms_contact_permission_attested_at",
            "timestamp with time zone",
            _sms_contact_permission_attested_at_field(),
        ),
    ]


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
        *_sms_contact_metadata_operations(
            "agentallowlistinvite",
            "api_agentallowlistinvite",
            permission_attested_help_text=SMS_INVITE_CONTACT_PERMISSION_ATTESTED_HELP,
        ),
        *_sms_contact_metadata_operations("commsallowlistentry", "api_commsallowlistentry"),
        *_sms_contact_metadata_operations("commsallowlistrequest", "api_commsallowlistrequest"),
        migrations.RunPython(add_switch, remove_switch),
    ]
