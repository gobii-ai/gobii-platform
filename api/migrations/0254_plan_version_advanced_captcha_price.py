import json
from typing import Any

from django.conf import settings
from django.db import migrations, models

from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanNames


def _parse_list_value(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, (list, tuple, set)):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _entry_value(entries_by_name: dict[str, Any], name: str) -> str:
    entry = entries_by_name.get(name)
    if not entry:
        return ""
    if getattr(entry, "is_secret", False):
        encrypted = getattr(entry, "value_encrypted", None)
        if not encrypted:
            return ""
        if isinstance(encrypted, memoryview):
            encrypted = encrypted.tobytes()
        try:
            from api.encryption import SecretsEncryption
            from cryptography.exceptions import InvalidTag
        except ImportError:
            return ""
        try:
            return SecretsEncryption.decrypt_value(encrypted)
        except (TypeError, ValueError, InvalidTag):
            return ""
    return getattr(entry, "value_text", "") or ""


def _entry_list(entries_by_name: dict[str, Any], name: str) -> list[str]:
    return _parse_list_value(_entry_value(entries_by_name, name))


def _plan_prefix(legacy_code: str) -> str:
    return PLAN_SLUG_BY_LEGACY_CODE.get(legacy_code, legacy_code)


def _plan_entry(entries_by_name: dict[str, Any], legacy_code: str, suffix: str) -> str:
    prefix = _plan_prefix(legacy_code)
    if prefix == "free":
        return ""
    return _entry_value(entries_by_name, f"{prefix}_{suffix}")


def _plan_entry_list(entries_by_name: dict[str, Any], legacy_code: str, suffix: str) -> list[str]:
    prefix = _plan_prefix(legacy_code)
    if prefix == "free":
        return []
    return _entry_list(entries_by_name, f"{prefix}_{suffix}")


def _get_entries_by_name(apps) -> dict[str, Any]:
    StripeConfig = apps.get_model("api", "StripeConfig")
    StripeConfigEntry = apps.get_model("api", "StripeConfigEntry")

    release_env = getattr(settings, "GOBII_RELEASE_ENV", None)
    config = None
    if release_env:
        config = StripeConfig.objects.filter(release_env=release_env).first()
    if config is None:
        config = StripeConfig.objects.first()
    if config is None:
        return {}

    entries = StripeConfigEntry.objects.filter(config=config)
    return {entry.name: entry for entry in entries}


def add_advanced_captcha_plan_prices(apps, schema_editor) -> None:
    PlanVersion = apps.get_model("api", "PlanVersion")
    PlanVersionPrice = apps.get_model("api", "PlanVersionPrice")

    entries_by_name = _get_entries_by_name(apps)

    for legacy_code in (PlanNames.STARTUP, PlanNames.SCALE, PlanNames.ORG_TEAM):
        slug = PLAN_SLUG_BY_LEGACY_CODE.get(legacy_code, legacy_code)
        version = PlanVersion.objects.filter(plan__slug=slug, version_code="v1").first()
        if not version:
            continue

        product_id = _plan_entry(entries_by_name, legacy_code, "advanced_captcha_resolution_product_id")
        price_ids: list[str] = []
        primary_price_id = _plan_entry(entries_by_name, legacy_code, "advanced_captcha_resolution_price_id")
        if primary_price_id:
            price_ids.append(primary_price_id)
        for price_id in _plan_entry_list(entries_by_name, legacy_code, "advanced_captcha_resolution_price_ids"):
            if price_id and price_id not in price_ids:
                price_ids.append(price_id)

        for price_id in price_ids:
            if not price_id:
                continue
            PlanVersionPrice.objects.get_or_create(
                price_id=price_id,
                defaults={
                    "plan_version": version,
                    "kind": "advanced_captcha_resolution",
                    "billing_interval": None,
                    "product_id": product_id or "",
                },
            )


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0253_addon_entitlement_advanced_captcha_resolution_delta"),
    ]

    operations = [
        migrations.AlterField(
            model_name="planversionprice",
            name="kind",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("base", "Base"),
                    ("seat", "Seat"),
                    ("overage", "Overage"),
                    ("task_pack", "Task pack"),
                    ("contact_pack", "Contact pack"),
                    ("browser_task_limit", "Browser task limit"),
                    ("advanced_captcha_resolution", "Advanced captcha resolution"),
                    ("dedicated_ip", "Dedicated IP"),
                ],
            ),
        ),
        migrations.RunPython(add_advanced_captcha_plan_prices, reverse_code=noop_reverse),
    ]
