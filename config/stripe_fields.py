"""Ordered metadata for entry-backed Stripe configuration values."""

import json

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class StripeValueKind(StrEnum):
    TEXT = "text"
    NONNEGATIVE_INTEGER = "nonnegative_integer"
    STRING_LIST = "string_list"
    SINGULAR_WITH_LEGACY_LIST = "singular_with_legacy_list"


@dataclass(frozen=True)
class StripeConfigFieldSpec:
    name: str
    value_kind: StripeValueKind
    env_name: str
    env_default: str | int | tuple[str, ...]
    admin_section: str
    admin_order: int
    label: str
    help_text: str = ""
    legacy_env_name: str | None = None
    legacy_entry_name: str | None = None


def _field(
    name: str,
    env_default: str,
    section: str,
    order: int,
    label: str,
    *,
    help_text: str = "",
) -> StripeConfigFieldSpec:
    return StripeConfigFieldSpec(
        name=name,
        value_kind=StripeValueKind.TEXT,
        env_name=f"STRIPE_{name.upper()}",
        env_default=env_default,
        admin_section=section,
        admin_order=order,
        label=label,
        help_text=help_text,
    )


def _integer(
    name: str,
    section: str,
    order: int,
    label: str,
    help_text: str,
) -> StripeConfigFieldSpec:
    spec = _field(name, "", section, order, label, help_text=help_text)
    return replace(spec, value_kind=StripeValueKind.NONNEGATIVE_INTEGER, env_default=0)


def _list(name: str, section: str, order: int, label: str, subject: str) -> StripeConfigFieldSpec:
    spec = _field(
        name,
        "",
        section,
        order,
        label,
        help_text=f"Comma-separated list of Stripe price IDs for {subject} tiers.",
    )
    return replace(spec, value_kind=StripeValueKind.STRING_LIST, env_default=())


def _captcha_price(name: str, section: str, order: int, label: str) -> StripeConfigFieldSpec:
    spec = _field(
        name,
        "",
        section,
        order,
        label,
        help_text="Stripe price ID for advanced captcha resolution.",
    )
    return replace(
        spec,
        value_kind=StripeValueKind.SINGULAR_WITH_LEGACY_LIST,
        legacy_env_name=f"STRIPE_{name.upper()}S",
        legacy_entry_name=f"{name}s",
    )


STARTUP = "Startup (Pro)"
SCALE = "Scale"
ORG_TEAM = "Org Team"
TASK_METERS = "Task Meters"

STRIPE_CONFIG_FIELDS = (
    _field("startup_product_id", "prod_dummy_startup", STARTUP, 60, "Startup product ID"),
    _field("startup_price_id", "price_dummy_startup", STARTUP, 10, "Startup base price ID"),
    _integer(
        "startup_trial_days",
        STARTUP,
        20,
        "Startup trial days",
        "Number of trial days for Startup checkout (0 disables trials).",
    ),
    _field(
        "startup_additional_task_price_id",
        "price_dummy_startup_additional_task",
        STARTUP,
        30,
        "Startup ad-hoc price ID",
    ),
    _field(
        "startup_task_pack_product_id",
        "prod_dummy_startup_task_pack_product",
        STARTUP,
        40,
        "Startup task pack product ID",
    ),
    _list("startup_task_pack_price_ids", STARTUP, 50, "Startup task pack price IDs", "task pack"),
    _field(
        "startup_contact_cap_product_id",
        "prod_dummy_startup_contact_cap",
        STARTUP,
        70,
        "Startup contact cap product ID",
    ),
    _list("startup_contact_cap_price_ids", STARTUP, 80, "Startup contact cap price IDs", "contact pack"),
    _field(
        "startup_browser_task_limit_product_id",
        "prod_dummy_startup_browser_task_limit",
        STARTUP,
        90,
        "Startup browser task limit product ID",
    ),
    _list(
        "startup_browser_task_limit_price_ids",
        STARTUP,
        100,
        "Startup browser task limit price IDs",
        "browser task limit",
    ),
    _field(
        "startup_advanced_captcha_resolution_product_id",
        "prod_dummy_startup_advanced_captcha_resolution",
        STARTUP,
        110,
        "Startup advanced captcha resolution product ID",
    ),
    _captcha_price(
        "startup_advanced_captcha_resolution_price_id",
        STARTUP,
        120,
        "Startup advanced captcha resolution price ID",
    ),
    _field(
        "scale_price_id",
        "price_dummy_scale",
        SCALE,
        20,
        "Scale base price ID",
    ),
    _integer(
        "scale_trial_days",
        SCALE,
        30,
        "Scale trial days",
        "Number of trial days for Scale checkout (0 disables trials).",
    ),
    _field(
        "scale_additional_task_price_id",
        "price_dummy_scale_additional_task",
        SCALE,
        40,
        "Scale ad-hoc task price ID",
    ),
    _field(
        "scale_task_pack_product_id",
        "prod_dummy_scale_task_pack_product",
        SCALE,
        50,
        "Scale task pack product ID",
    ),
    _list("scale_task_pack_price_ids", SCALE, 60, "Scale task pack price IDs", "task pack"),
    _field("scale_product_id", "prod_dummy_scale", SCALE, 10, "Scale product ID"),
    _field(
        "scale_contact_cap_product_id",
        "prod_dummy_scale_contact_cap",
        SCALE,
        70,
        "Scale contact cap product ID",
    ),
    _list("scale_contact_cap_price_ids", SCALE, 80, "Scale contact cap price IDs", "contact pack"),
    _field(
        "scale_browser_task_limit_product_id",
        "prod_dummy_scale_browser_task_limit",
        SCALE,
        90,
        "Scale browser task limit product ID",
    ),
    _list(
        "scale_browser_task_limit_price_ids",
        SCALE,
        100,
        "Scale browser task limit price IDs",
        "browser task limit",
    ),
    _field(
        "scale_advanced_captcha_resolution_product_id",
        "prod_dummy_scale_advanced_captcha_resolution",
        SCALE,
        110,
        "Scale advanced captcha resolution product ID",
    ),
    _captcha_price(
        "scale_advanced_captcha_resolution_price_id",
        SCALE,
        120,
        "Scale advanced captcha resolution price ID",
    ),
    _field(
        "startup_dedicated_ip_product_id",
        "prod_dummy_startup_dedicated_ip",
        STARTUP,
        140,
        "Pro dedicated IP product ID",
    ),
    _field(
        "startup_dedicated_ip_price_id",
        "price_dummy_startup_dedicated_ip",
        STARTUP,
        130,
        "Pro dedicated IP price ID",
    ),
    _field(
        "scale_dedicated_ip_product_id",
        "prod_dummy_scale_dedicated_ip",
        SCALE,
        130,
        "Scale dedicated IP product ID",
    ),
    _field(
        "scale_dedicated_ip_price_id",
        "price_dummy_scale_dedicated_ip",
        SCALE,
        140,
        "Scale dedicated IP price ID",
    ),
    _field("org_team_product_id", "prod_dummy_org_team", ORG_TEAM, 20, "Org/Team product ID"),
    _field("org_team_price_id", "price_dummy_org_team", ORG_TEAM, 10, "Org/Team price ID"),
    _field(
        "org_team_additional_task_product_id",
        "prod_dummy_org_team_additional_task",
        ORG_TEAM,
        50,
        "Org/Team ad-hoc task product ID",
    ),
    _field(
        "org_team_additional_task_price_id",
        "price_dummy_org_team_additional_task",
        ORG_TEAM,
        60,
        "Org/Team ad-hoc task price ID",
    ),
    _field(
        "org_team_task_pack_product_id",
        "prod_dummy_org_team_task_pack_product",
        ORG_TEAM,
        70,
        "Org/Team task pack product ID",
    ),
    _list("org_team_task_pack_price_ids", ORG_TEAM, 80, "Org/Team task pack price IDs", "task pack"),
    _field(
        "org_team_contact_cap_product_id",
        "prod_dummy_org_team_contact_cap",
        ORG_TEAM,
        90,
        "Org/Team contact cap product ID",
    ),
    _list("org_team_contact_cap_price_ids", ORG_TEAM, 100, "Org/Team contact cap price IDs", "contact pack"),
    _field(
        "org_team_browser_task_limit_product_id",
        "prod_dummy_org_team_browser_task_limit",
        ORG_TEAM,
        110,
        "Org/Team browser task limit product ID",
    ),
    _list(
        "org_team_browser_task_limit_price_ids",
        ORG_TEAM,
        120,
        "Org/Team browser task limit price IDs",
        "browser task limit",
    ),
    _field(
        "org_team_advanced_captcha_resolution_product_id",
        "prod_dummy_org_team_advanced_captcha_resolution",
        ORG_TEAM,
        130,
        "Org/Team advanced captcha resolution product ID",
    ),
    _captcha_price(
        "org_team_advanced_captcha_resolution_price_id",
        ORG_TEAM,
        140,
        "Org/Team advanced captcha resolution price ID",
    ),
    _field(
        "org_team_dedicated_ip_product_id",
        "prod_dummy_org_dedicated_ip",
        ORG_TEAM,
        40,
        "Org/Team dedicated IP product ID",
    ),
    _field(
        "org_team_dedicated_ip_price_id",
        "price_dummy_org_dedicated_ip",
        ORG_TEAM,
        30,
        "Org/Team dedicated IP price ID",
    ),
    _field("task_meter_id", "meter_dummy_task", TASK_METERS, 10, "Task meter ID"),
    _field("task_meter_event_name", "task", TASK_METERS, 20, "Task meter event name"),
    StripeConfigFieldSpec(
        name="org_task_meter_id",
        value_kind=StripeValueKind.TEXT,
        env_name="STRIPE_ORG_TASK_METER_ID",
        env_default="meter_dummy_org_task",
        admin_section=TASK_METERS,
        admin_order=50,
        label="Organization task meter ID",
    ),
    StripeConfigFieldSpec(
        name="org_team_task_meter_id",
        value_kind=StripeValueKind.TEXT,
        env_name="STRIPE_ORG_TASK_METER_ID",
        env_default="meter_dummy_org_task",
        admin_section=TASK_METERS,
        admin_order=30,
        label="Org/Team task meter ID",
    ),
    StripeConfigFieldSpec(
        name="org_team_task_meter_event_name",
        value_kind=StripeValueKind.TEXT,
        env_name="STRIPE_ORG_TASK_METER_EVENT_NAME",
        env_default="task_org_team_task_meter_name",
        admin_section=TASK_METERS,
        admin_order=40,
        label="Org/Team task meter event name",
    ),
)

STRIPE_CONFIG_FIELDS_BY_NAME = {spec.name: spec for spec in STRIPE_CONFIG_FIELDS}
STRIPE_LEGACY_ENTRY_NAMES = tuple(
    spec.legacy_entry_name for spec in STRIPE_CONFIG_FIELDS if spec.legacy_entry_name
)


def admin_fields(section: str) -> tuple[str, ...]:
    specs = sorted(
        (spec for spec in STRIPE_CONFIG_FIELDS if spec.admin_section == section),
        key=lambda spec: spec.admin_order,
    )
    return tuple(spec.name for spec in specs)


def parse_nonnegative_integer(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return max(int(str(value).strip()), 0)
    except (TypeError, ValueError):
        return default


def parse_string_list(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    values = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, tuple, set)):
                values = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            values = value.split(",")

    result: list[str] = []
    for candidate in values:
        text = str(candidate).strip() if candidate else ""
        if text and text not in result:
            result.append(text)
    return tuple(result)


def parse_stored_string_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parsed_values: list[Any] = []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, (list, tuple, set)):
            parsed_values = list(parsed)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    values = parsed_values or [part.strip() for part in str(value).split(",")]
    return tuple(item for item in (str(candidate).strip() for candidate in values if candidate) if item)


def first_string(value: Any) -> str:
    return next(iter(parse_string_list(value)), "")
