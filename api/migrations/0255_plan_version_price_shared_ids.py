from typing import Any

from django.conf import settings
from django.db import migrations, models

from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanNames


def _parse_list_value(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        import json

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


def _normalize_price_list(*values: Any) -> list[str]:
    seen: list[str] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = str(value).split(",")
        for candidate in candidates:
            cid = str(candidate).strip()
            if cid and cid not in seen:
                seen.append(cid)
    return seen


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


def _infer_billing_interval(apps, price_id: str) -> str | None:
    if not price_id:
        return None
    try:
        Price = apps.get_model("djstripe", "Price")
    except LookupError:
        return None

    try:
        price_obj = Price.objects.filter(id=price_id).first()
    except Exception:
        return None

    if not price_obj:
        return None

    recurring = getattr(price_obj, "recurring", None)
    interval = None
    if isinstance(recurring, dict):
        interval = recurring.get("interval")
    if not interval:
        interval = getattr(price_obj, "interval", None)
    if interval in ("month", "year"):
        return interval
    return None


def _upsert_price(PlanVersionPrice, plan_version, price_id: str, kind: str, product_id: str, billing_interval: str | None) -> None:
    if not price_id:
        return
    PlanVersionPrice.objects.update_or_create(
        plan_version=plan_version,
        price_id=price_id,
        defaults={
            "kind": kind,
            "product_id": product_id or "",
            "billing_interval": billing_interval,
        },
    )


def populate_plan_version_prices(apps, schema_editor) -> None:
    PlanVersion = apps.get_model("api", "PlanVersion")
    PlanVersionPrice = apps.get_model("api", "PlanVersionPrice")
    entries_by_name = _get_entries_by_name(apps)
    if not entries_by_name:
        return

    for legacy_code in (PlanNames.STARTUP, PlanNames.SCALE, PlanNames.ORG_TEAM):
        slug = PLAN_SLUG_BY_LEGACY_CODE.get(legacy_code, legacy_code)
        version = PlanVersion.objects.filter(plan__slug=slug, version_code="v1").first()
        if not version:
            continue

        base_price_id = _plan_entry(entries_by_name, legacy_code, "price_id")
        base_product_id = _plan_entry(entries_by_name, legacy_code, "product_id")
        base_kind = "seat" if legacy_code == PlanNames.ORG_TEAM else "base"
        _upsert_price(
            PlanVersionPrice,
            version,
            base_price_id,
            base_kind,
            base_product_id,
            _infer_billing_interval(apps, base_price_id),
        )

        additional_task_price_id = _plan_entry(entries_by_name, legacy_code, "additional_task_price_id")
        additional_task_product_id = _plan_entry(entries_by_name, legacy_code, "additional_task_product_id")
        _upsert_price(
            PlanVersionPrice,
            version,
            additional_task_price_id,
            "overage",
            additional_task_product_id,
            None,
        )

        dedicated_ip_price_id = _plan_entry(entries_by_name, legacy_code, "dedicated_ip_price_id")
        dedicated_ip_product_id = _plan_entry(entries_by_name, legacy_code, "dedicated_ip_product_id")
        _upsert_price(
            PlanVersionPrice,
            version,
            dedicated_ip_price_id,
            "dedicated_ip",
            dedicated_ip_product_id,
            None,
        )

        task_pack_ids = _plan_entry_list(entries_by_name, legacy_code, "task_pack_price_ids")
        task_pack_product_id = _plan_entry(entries_by_name, legacy_code, "task_pack_product_id")
        for price_id in task_pack_ids:
            _upsert_price(PlanVersionPrice, version, price_id, "task_pack", task_pack_product_id, None)

        contact_pack_ids = _plan_entry_list(entries_by_name, legacy_code, "contact_cap_price_ids")
        contact_pack_product_id = _plan_entry(entries_by_name, legacy_code, "contact_cap_product_id")
        for price_id in contact_pack_ids:
            _upsert_price(PlanVersionPrice, version, price_id, "contact_pack", contact_pack_product_id, None)

        browser_pack_ids = _plan_entry_list(entries_by_name, legacy_code, "browser_task_limit_price_ids")
        browser_pack_product_id = _plan_entry(entries_by_name, legacy_code, "browser_task_limit_product_id")
        for price_id in browser_pack_ids:
            _upsert_price(PlanVersionPrice, version, price_id, "browser_task_limit", browser_pack_product_id, None)

        advanced_single = _plan_entry(entries_by_name, legacy_code, "advanced_captcha_resolution_price_id")
        advanced_list = _plan_entry_list(entries_by_name, legacy_code, "advanced_captcha_resolution_price_ids")
        advanced_ids = _normalize_price_list(advanced_single, advanced_list)
        advanced_product_id = _plan_entry(entries_by_name, legacy_code, "advanced_captcha_resolution_product_id")
        for price_id in advanced_ids:
            _upsert_price(
                PlanVersionPrice,
                version,
                price_id,
                "advanced_captcha_resolution",
                advanced_product_id,
                None,
            )


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0254_plan_version_advanced_captcha_price"),
    ]

    operations = [
        migrations.AlterField(
            model_name="planversionprice",
            name="price_id",
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name="planversionprice",
            constraint=models.UniqueConstraint(
                fields=("plan_version", "price_id"),
                name="unique_plan_version_price_id",
            ),
        ),
        migrations.RunPython(populate_plan_version_prices, reverse_code=noop_reverse),
    ]
