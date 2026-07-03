import json

from django.db import migrations


META_ADS_SKILL_KEY = "meta_ads_platform"
META_ADS_PROVIDER_KEY = "meta_ads"
META_ADS_SECRET_KEY = "native_meta_ads"
INTEGRATION_SECRET_TYPE = "integration"
INTEGRATION_DOMAIN_SENTINEL = "__gobii_integration__"
META_ADS_ALLOWED_KEYS = {
    "META_APP_ID",
    "META_APP_SECRET",
    "META_SYSTEM_USER_TOKEN",
    "META_AD_ACCOUNT_ID",
    "META_API_VERSION",
    "META_BUSINESS_ID",
    "META_DATASET_ID",
}


def migrate_default_meta_ads_profiles(apps, schema_editor):
    SystemSkillProfile = apps.get_model("api", "SystemSkillProfile")
    SystemSkillProfileSecret = apps.get_model("api", "SystemSkillProfileSecret")
    GlobalSecret = apps.get_model("api", "GlobalSecret")

    from api.encryption import SecretsEncryption

    selected_owner_keys = set()
    profiles = SystemSkillProfile.objects.filter(skill_key=META_ADS_SKILL_KEY).order_by(
        "user_id",
        "organization_id",
        "-is_default",
        "label",
        "profile_key",
    )
    for profile in profiles.iterator():
        owner_key = (profile.user_id, profile.organization_id)
        if owner_key in selected_owner_keys:
            continue
        selected_owner_keys.add(owner_key)

        values = {}
        secrets = SystemSkillProfileSecret.objects.filter(profile_id=profile.id).order_by("key")
        for secret in secrets.iterator():
            key = str(secret.key or "").strip().upper()
            if key not in META_ADS_ALLOWED_KEYS:
                continue
            values[key] = SecretsEncryption.decrypt_value(secret.encrypted_value)

        if not values:
            continue

        values.setdefault("META_API_VERSION", "v25.0")
        credentials = {
            "provider_key": META_ADS_PROVIDER_KEY,
            "auth_type": "manual",
            **values,
            "metadata": {
                "migrated_from_system_skill_profile": {
                    "id": str(profile.id),
                    "profile_key": profile.profile_key,
                    "label": profile.label,
                    "is_default": profile.is_default,
                },
                "api_hosts": ["graph.facebook.com"],
                "api_url_prefixes": ["https://graph.facebook.com/"],
                "credential_fields": sorted(META_ADS_ALLOWED_KEYS),
            },
        }
        encrypted_value = SecretsEncryption.encrypt_value(
            json.dumps(credentials, separators=(",", ":"), sort_keys=True)
        )

        filters = {
            "secret_type": INTEGRATION_SECRET_TYPE,
            "domain_pattern": INTEGRATION_DOMAIN_SENTINEL,
            "key": META_ADS_SECRET_KEY,
        }
        if profile.organization_id:
            filters["organization_id"] = profile.organization_id
        else:
            filters["user_id"] = profile.user_id
            filters["organization_id"] = None

        existing = GlobalSecret.objects.filter(**filters).first()
        defaults = {
            "user_id": None if profile.organization_id else profile.user_id,
            "organization_id": profile.organization_id,
            "name": "Meta Ads",
            "description": "Connect Meta Ads for account health checks, campaign reporting, and conversion quality monitoring.",
            "secret_type": INTEGRATION_SECRET_TYPE,
            "domain_pattern": INTEGRATION_DOMAIN_SENTINEL,
            "key": META_ADS_SECRET_KEY,
            "encrypted_value": encrypted_value,
        }
        if existing is None:
            GlobalSecret.objects.create(**defaults)
        else:
            for field, value in defaults.items():
                setattr(existing, field, value)
            existing.save(update_fields=list(defaults.keys()) + ["updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0409_merge_20260702_1712"),
    ]

    operations = [
        migrations.RunPython(migrate_default_meta_ads_profiles, migrations.RunPython.noop),
        migrations.DeleteModel(name="SystemSkillProfileSecret"),
        migrations.DeleteModel(name="SystemSkillProfile"),
    ]
