import json
from typing import Any

from django.conf import settings
from django.db import migrations

from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanNames


ENTITLEMENT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "max_contacts_per_agent": {
        "display_name": "Max contacts per agent",
        "description": "Maximum contacts each agent can manage.",
        "value_type": "int",
        "unit": "contacts",
    },
    "max_agents": {
        "display_name": "Max agents",
        "description": "Maximum number of agents allowed.",
        "value_type": "int",
        "unit": "agents",
    },
    "api_rate_limit": {
        "display_name": "API rate limit",
        "description": "Maximum API requests per minute.",
        "value_type": "int",
        "unit": "requests/min",
    },
    "monthly_task_credits": {
        "display_name": "Monthly task credits",
        "description": "Included monthly task credits.",
        "value_type": "int",
        "unit": "credits",
    },
    "credits_per_seat": {
        "display_name": "Credits per seat",
        "description": "Included credits per seat for organization plans.",
        "value_type": "int",
        "unit": "credits",
    },
    "additional_tasks_allowed": {
        "display_name": "Additional tasks allowed",
        "description": "Whether overage tasks can be purchased.",
        "value_type": "bool",
        "unit": "",
    },
    "additional_task_limit_default": {
        "display_name": "Additional task limit default",
        "description": "Default additional task limit when enabled.",
        "value_type": "int",
        "unit": "tasks",
    },
    "has_dedicated_ip": {
        "display_name": "Dedicated IP support",
        "description": "Whether dedicated IP is available.",
        "value_type": "bool",
        "unit": "",
    },
}

PLAN_CONFIG_SNAPSHOT: dict[str, dict[str, Any]] = {
    PlanNames.FREE: {
        "name": "Free",
        "description": "Free plan with basic features and limited support.",
        "monthly_task_credits": 100,
        "api_rate_limit": 60,
        "agent_limit": 5,
        "max_contacts_per_agent": 3,
        "credits_per_seat": 0,
        "org": False,
    },
    PlanNames.STARTUP: {
        "name": "Pro",
        "description": "Pro plan with enhanced features and support.",
        "monthly_task_credits": 500,
        "api_rate_limit": 600,
        "agent_limit": -2147483648,
        "max_contacts_per_agent": 20,
        "credits_per_seat": 0,
        "org": False,
    },
    PlanNames.SCALE: {
        "name": "Scale",
        "description": "Scale plan with enhanced limits and support.",
        "monthly_task_credits": 10000,
        "api_rate_limit": 1500,
        "agent_limit": -2147483648,
        "max_contacts_per_agent": 50,
        "credits_per_seat": 0,
        "org": False,
    },
    PlanNames.ORG_TEAM: {
        "name": "Team",
        "description": "Team plan with collaboration features and priority support.",
        "monthly_task_credits": 2000,
        "api_rate_limit": 2000,
        "agent_limit": -2147483648,
        "max_contacts_per_agent": 50,
        "credits_per_seat": 500,
        "org": True,
    },
}


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
        except Exception:
            return ""
        try:
            return SecretsEncryption.decrypt_value(encrypted)
        except Exception:
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


def seed_plans(apps, schema_editor) -> None:
    Plan = apps.get_model("api", "Plan")
    PlanVersion = apps.get_model("api", "PlanVersion")
    PlanVersionPrice = apps.get_model("api", "PlanVersionPrice")
    EntitlementDefinition = apps.get_model("api", "EntitlementDefinition")
    PlanVersionEntitlement = apps.get_model("api", "PlanVersionEntitlement")

    entries_by_name = _get_entries_by_name(apps)

    entitlement_models: dict[str, Any] = {}
    for key, payload in ENTITLEMENT_DEFINITIONS.items():
        obj, _ = EntitlementDefinition.objects.get_or_create(
            key=key,
            defaults={
                "display_name": payload["display_name"],
                "description": payload["description"],
                "value_type": payload["value_type"],
                "unit": payload["unit"],
            },
        )
        entitlement_models[key] = obj

    def _get_or_create_plan(legacy_code: str, config: dict[str, Any]):
        slug = PLAN_SLUG_BY_LEGACY_CODE.get(legacy_code, legacy_code)
        is_org = bool(config.get("org"))
        plan, created = Plan.objects.get_or_create(
            slug=slug,
            defaults={"is_org": is_org, "is_active": True},
        )
        updates: list[str] = []
        if plan.is_org != is_org:
            plan.is_org = is_org
            updates.append("is_org")
        if not plan.is_active:
            plan.is_active = True
            updates.append("is_active")
        if updates:
            plan.save(update_fields=updates)
        return plan

    for legacy_code, config in PLAN_CONFIG_SNAPSHOT.items():
        plan = _get_or_create_plan(legacy_code, config)
        version, created = PlanVersion.objects.get_or_create(
            plan=plan,
            version_code="v1",
            defaults={
                "legacy_plan_code": legacy_code,
                "is_active_for_new_subs": True,
                "display_name": config.get("name") or plan.slug,
                "tagline": "",
                "description": config.get("description") or "",
                "marketing_features": [],
            },
        )
        updates: list[str] = []
        if not version.legacy_plan_code:
            version.legacy_plan_code = legacy_code
            updates.append("legacy_plan_code")
        if not version.display_name:
            version.display_name = config.get("name") or plan.slug
            updates.append("display_name")
        if not version.description:
            version.description = config.get("description") or ""
            updates.append("description")
        if version.is_active_for_new_subs is False:
            if not PlanVersion.objects.filter(plan=plan, is_active_for_new_subs=True).exists():
                version.is_active_for_new_subs = True
                updates.append("is_active_for_new_subs")
        if updates:
            version.save(update_fields=updates)

        additional_task_price_id = _plan_entry(
            entries_by_name,
            legacy_code,
            "additional_task_price_id",
        )
        additional_task_product_id = _plan_entry(
            entries_by_name,
            legacy_code,
            "additional_task_product_id",
        )

        dedicated_ip_price_id = _plan_entry(
            entries_by_name,
            legacy_code,
            "dedicated_ip_price_id",
        )
        dedicated_product_id = _plan_entry(
            entries_by_name,
            legacy_code,
            "dedicated_ip_product_id",
        )

        entitlements = {
            "max_contacts_per_agent": int(config.get("max_contacts_per_agent") or 0),
            "max_agents": int(config.get("agent_limit") or 0),
            "api_rate_limit": int(config.get("api_rate_limit") or 0),
            "monthly_task_credits": int(config.get("monthly_task_credits") or 0),
            "credits_per_seat": int(config.get("credits_per_seat") or 0),
            "additional_tasks_allowed": bool(additional_task_price_id),
            "additional_task_limit_default": 0,
            "has_dedicated_ip": bool(dedicated_ip_price_id),
        }

        for key, value in entitlements.items():
            entitlement = entitlement_models[key]
            value_type = entitlement.value_type
            defaults = {
                "value_int": None,
                "value_decimal": None,
                "value_bool": None,
                "value_text": None,
                "value_json": None,
            }
            if value_type == "int":
                defaults["value_int"] = int(value)
            elif value_type == "decimal":
                defaults["value_decimal"] = value
            elif value_type == "bool":
                defaults["value_bool"] = bool(value)
            elif value_type == "text":
                defaults["value_text"] = str(value)
            else:
                defaults["value_json"] = value

            PlanVersionEntitlement.objects.update_or_create(
                plan_version=version,
                entitlement=entitlement,
                defaults=defaults,
            )

        base_price_id = _plan_entry(entries_by_name, legacy_code, "price_id")
        product_id = _plan_entry(entries_by_name, legacy_code, "product_id")

        if base_price_id:
            PlanVersionPrice.objects.get_or_create(
                price_id=base_price_id,
                defaults={
                    "plan_version": version,
                    "kind": "seat" if legacy_code == PlanNames.ORG_TEAM else "base",
                    "billing_interval": _infer_billing_interval(apps, base_price_id),
                    "product_id": product_id,
                },
            )

        if additional_task_price_id:
            PlanVersionPrice.objects.get_or_create(
                price_id=additional_task_price_id,
                defaults={
                    "plan_version": version,
                    "kind": "overage",
                    "billing_interval": None,
                    "product_id": additional_task_product_id,
                },
            )

        if dedicated_ip_price_id:
            PlanVersionPrice.objects.get_or_create(
                price_id=dedicated_ip_price_id,
                defaults={
                    "plan_version": version,
                    "kind": "dedicated_ip",
                    "billing_interval": None,
                    "product_id": dedicated_product_id,
                },
            )

        task_pack_ids = _plan_entry_list(entries_by_name, legacy_code, "task_pack_price_ids")
        contact_pack_ids = _plan_entry_list(entries_by_name, legacy_code, "contact_cap_price_ids")
        browser_pack_ids = _plan_entry_list(entries_by_name, legacy_code, "browser_task_limit_price_ids")
        task_product_id = _plan_entry(entries_by_name, legacy_code, "task_pack_product_id")
        contact_product_id = _plan_entry(entries_by_name, legacy_code, "contact_cap_product_id")
        browser_product_id = _plan_entry(entries_by_name, legacy_code, "browser_task_limit_product_id")

        for price_id in task_pack_ids:
            if not price_id:
                continue
            PlanVersionPrice.objects.get_or_create(
                price_id=price_id,
                defaults={
                    "plan_version": version,
                    "kind": "task_pack",
                    "billing_interval": None,
                    "product_id": task_product_id,
                },
            )

        for price_id in contact_pack_ids:
            if not price_id:
                continue
            PlanVersionPrice.objects.get_or_create(
                price_id=price_id,
                defaults={
                    "plan_version": version,
                    "kind": "contact_pack",
                    "billing_interval": None,
                    "product_id": contact_product_id,
                },
            )

        for price_id in browser_pack_ids:
            if not price_id:
                continue
            PlanVersionPrice.objects.get_or_create(
                price_id=price_id,
                defaults={
                    "plan_version": version,
                    "kind": "browser_task_limit",
                    "billing_interval": None,
                    "product_id": browser_product_id,
                },
            )

    plan_versions_by_legacy = {
        pv.legacy_plan_code.lower(): pv
        for pv in PlanVersion.objects.exclude(legacy_plan_code__isnull=True)
        if pv.legacy_plan_code
    }

    UserBilling = apps.get_model("api", "UserBilling")
    OrganizationBilling = apps.get_model("api", "OrganizationBilling")
    DailyCreditConfig = apps.get_model("api", "DailyCreditConfig")
    BrowserConfig = apps.get_model("api", "BrowserConfig")
    ToolConfig = apps.get_model("api", "ToolConfig")

    for billing in UserBilling.objects.filter(plan_version__isnull=True):
        legacy_code = (billing.subscription or "").lower()
        plan_version = plan_versions_by_legacy.get(legacy_code)
        if plan_version:
            billing.plan_version_id = plan_version.id
            billing.save(update_fields=["plan_version"])

    for billing in OrganizationBilling.objects.filter(plan_version__isnull=True):
        legacy_code = (billing.subscription or "").lower()
        plan_version = plan_versions_by_legacy.get(legacy_code)
        if plan_version:
            billing.plan_version_id = plan_version.id
            billing.save(update_fields=["plan_version"])

    for config in DailyCreditConfig.objects.filter(plan_version__isnull=True).exclude(plan_name__isnull=True):
        legacy_code = (config.plan_name or "").lower()
        plan_version = plan_versions_by_legacy.get(legacy_code)
        if plan_version:
            config.plan_version_id = plan_version.id
            config.save(update_fields=["plan_version"])

    for config in BrowserConfig.objects.filter(plan_version__isnull=True).exclude(plan_name__isnull=True):
        legacy_code = (config.plan_name or "").lower()
        plan_version = plan_versions_by_legacy.get(legacy_code)
        if plan_version:
            config.plan_version_id = plan_version.id
            config.save(update_fields=["plan_version"])

    for config in ToolConfig.objects.filter(plan_version__isnull=True).exclude(plan_name__isnull=True):
        legacy_code = (config.plan_name or "").lower()
        plan_version = plan_versions_by_legacy.get(legacy_code)
        if plan_version:
            config.plan_version_id = plan_version.id
            config.save(update_fields=["plan_version"])


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0247_plan_versioning"),
    ]

    operations = [
        migrations.RunPython(seed_plans, reverse_code=noop_reverse),
    ]
