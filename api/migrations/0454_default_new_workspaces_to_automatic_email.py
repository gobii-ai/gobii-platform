from django.conf import settings
from django.db import migrations
from django.db.models import Q


DEFAULT_PREFERENCE_KEY = "agent.email.default_sending_mode"
ORGANIZATION_DEFAULT_KEY = "default_email_sending_mode"
PILOT_DEFAULT_MODE = "review_all_external"
LEGACY_DEFAULT_MODE = "send_automatically"
VALID_MODES = {
    "review_all_external",
    "review_new_contacts",
    "send_automatically",
}
BATCH_SIZE = 1000


def _bulk_update_preferences(UserPreference, preferences):
    if preferences:
        UserPreference.objects.bulk_update(
            preferences,
            ["preferences"],
            batch_size=BATCH_SIZE,
        )


def _bulk_update_organizations(Organization, organizations):
    if organizations:
        Organization.objects.bulk_update(
            organizations,
            ["org_settings"],
            batch_size=BATCH_SIZE,
        )


def pin_existing_workspace_email_sending_defaults(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    UserPreference = apps.get_model("api", "UserPreference")
    Organization = apps.get_model("api", "Organization")
    PersistentAgent = apps.get_model("api", "PersistentAgent")

    staff_user_ids = set(
        User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).values_list("pk", flat=True)
    )
    pilot_personal_user_ids = set(
        PersistentAgent.objects.filter(
            organization__isnull=True,
            outbound_email_reviews__isnull=False,
        ).values_list("user_id", flat=True)
    )
    pilot_personal_user_ids.update(staff_user_ids)
    pilot_organization_ids = set(
        PersistentAgent.objects.filter(
            organization__isnull=False,
            outbound_email_reviews__isnull=False,
        ).values_list("organization_id", flat=True)
    )

    preference_updates = []
    for preference in UserPreference.objects.all().iterator(chunk_size=BATCH_SIZE):
        stored = preference.preferences if isinstance(preference.preferences, dict) else {}
        if stored.get(DEFAULT_PREFERENCE_KEY) in VALID_MODES:
            continue
        preference.preferences = {
            **stored,
            DEFAULT_PREFERENCE_KEY: (
                PILOT_DEFAULT_MODE
                if preference.user_id in pilot_personal_user_ids
                else LEGACY_DEFAULT_MODE
            ),
        }
        preference_updates.append(preference)
        if len(preference_updates) == BATCH_SIZE:
            _bulk_update_preferences(UserPreference, preference_updates)
            preference_updates = []
    _bulk_update_preferences(UserPreference, preference_updates)

    users_with_preferences = UserPreference.objects.values("user_id")
    missing_preferences = []
    missing_user_ids = (
        User.objects.exclude(pk__in=users_with_preferences)
        .values_list("pk", flat=True)
        .iterator(chunk_size=BATCH_SIZE)
    )
    for user_id in missing_user_ids:
        missing_preferences.append(
            UserPreference(
                user_id=user_id,
                preferences={
                    DEFAULT_PREFERENCE_KEY: (
                        PILOT_DEFAULT_MODE
                        if user_id in pilot_personal_user_ids
                        else LEGACY_DEFAULT_MODE
                    )
                },
            )
        )
        if len(missing_preferences) == BATCH_SIZE:
            UserPreference.objects.bulk_create(missing_preferences, batch_size=BATCH_SIZE)
            missing_preferences = []
    if missing_preferences:
        UserPreference.objects.bulk_create(missing_preferences, batch_size=BATCH_SIZE)

    organization_updates = []
    for organization in Organization.objects.all().iterator(chunk_size=BATCH_SIZE):
        org_settings = organization.org_settings if isinstance(organization.org_settings, dict) else {}
        if org_settings.get(ORGANIZATION_DEFAULT_KEY) in VALID_MODES:
            continue
        organization.org_settings = {
            **org_settings,
            ORGANIZATION_DEFAULT_KEY: (
                PILOT_DEFAULT_MODE
                if (
                    organization.created_by_id in staff_user_ids
                    or organization.pk in pilot_organization_ids
                )
                else LEGACY_DEFAULT_MODE
            ),
        }
        organization_updates.append(organization)
        if len(organization_updates) == BATCH_SIZE:
            _bulk_update_organizations(Organization, organization_updates)
            organization_updates = []
    _bulk_update_organizations(Organization, organization_updates)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0453_merge_20260807_1435"),
    ]

    operations = [
        migrations.RunPython(
            pin_existing_workspace_email_sending_defaults,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
