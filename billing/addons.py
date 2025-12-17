from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from django.apps import apps
from django.db import transaction, DatabaseError
from django.db.models import F, IntegerField, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from config.stripe_config import get_stripe_settings
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames


@dataclass(frozen=True)
class AddonUplift:
    task_credits: int = 0
    contact_cap: int = 0


@dataclass(frozen=True)
class AddonPriceConfig:
    price_id: str
    product_id: str
    task_credits_delta: int = 0
    contact_cap_delta: int = 0
    unit_amount: int | None = None
    currency: str = ""


class AddonEntitlementService:
    """Helpers for aggregating active add-on entitlements."""

    @staticmethod
    def _resolve_price_ids(owner_type: str, plan_id: str | None) -> dict[str, str]:
        def _normalize_price_list(*values: Any) -> list[str]:
            ids: list[str] = []
            for raw in values:
                if not raw:
                    continue
                if isinstance(raw, (list, tuple, set)):
                    candidates = raw
                else:
                    text = str(raw).strip()
                    if not text:
                        continue
                    candidates = [part.strip() for part in text.split(",")]
                for candidate in candidates:
                    if not candidate:
                        continue
                    cid = str(candidate).strip()
                    if cid and cid not in ids:
                        ids.append(cid)
            return ids

        stripe_settings = get_stripe_settings()
        if owner_type == "organization":
            return {
                "task_pack": _normalize_price_list(
                    getattr(stripe_settings, "org_team_task_pack_price_ids", ()),
                    getattr(stripe_settings, "org_team_task_pack_price_id", ""),
                ),
                "contact_pack": _normalize_price_list(
                    getattr(stripe_settings, "org_team_contact_cap_price_ids", ()),
                    getattr(stripe_settings, "org_team_contact_cap_price_id", ""),
                ),
            }

        if plan_id == PlanNames.STARTUP:
            return {
                "task_pack": _normalize_price_list(
                    getattr(stripe_settings, "startup_task_pack_price_ids", ()),
                    getattr(stripe_settings, "startup_task_pack_price_id", ""),
                ),
                "contact_pack": _normalize_price_list(
                    getattr(stripe_settings, "startup_contact_cap_price_ids", ()),
                    getattr(stripe_settings, "startup_contact_cap_price_id", ""),
                ),
            }

        if plan_id == PlanNames.SCALE:
            return {
                "task_pack": _normalize_price_list(
                    getattr(stripe_settings, "scale_task_pack_price_ids", ()),
                    getattr(stripe_settings, "scale_task_pack_price_id", ""),
                ),
                "contact_pack": _normalize_price_list(
                    getattr(stripe_settings, "scale_contact_cap_price_ids", ()),
                    getattr(stripe_settings, "scale_contact_cap_price_id", ""),
                ),
            }

        return {}

    @staticmethod
    def _get_model():
        return apps.get_model("api", "AddonEntitlement")

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _extract_price_config(price_id: str, price_data: Mapping[str, Any] | None = None) -> AddonPriceConfig | None:
        """Return deltas for a Stripe price from embedded or cached metadata."""
        if not price_id:
            return None

        metadata: Mapping[str, Any] = {}
        product_id = ""
        unit_amount: int | None = None
        currency = ""

        if isinstance(price_data, Mapping):
            metadata = price_data.get("metadata") or {}
            product_id = price_data.get("product") or ""
            currency = (price_data.get("currency") or "").lower()
            unit_amount = AddonEntitlementService._safe_int(
                price_data.get("unit_amount") or price_data.get("unit_amount_decimal")
            )
            if isinstance(product_id, Mapping):
                product_id = product_id.get("id") or ""
        try:
            if not metadata:
                Price = apps.get_model("djstripe", "Price")
                price_obj = Price.objects.filter(id=price_id).select_related("product").first()
                if price_obj:
                    metadata = getattr(price_obj, "metadata", {}) or {}
                    product_obj = getattr(price_obj, "product", None)
                    product_id = product_id or getattr(product_obj, "id", "") or ""
                    currency = currency or getattr(price_obj, "currency", "") or ""
                    try:
                        unit_amount = unit_amount or AddonEntitlementService._safe_int(getattr(price_obj, "unit_amount", None))
                    except Exception:
                        unit_amount = unit_amount or None
        except (LookupError, DatabaseError):
            # Best-effort only; metadata is optional for tests and local dev
            metadata = metadata or {}

        task_delta = AddonEntitlementService._safe_int(
            metadata.get("task_credits_delta")
            or metadata.get("task_credit_delta")
            or metadata.get("task_pack_credits")
        )
        contact_delta = AddonEntitlementService._safe_int(
            metadata.get("contact_cap_delta")
            or metadata.get("contact_cap")
            or metadata.get("contacts_delta")
        )

        if task_delta == 0 and contact_delta == 0:
            return None

        return AddonPriceConfig(
            price_id=price_id,
            product_id=str(product_id or ""),
            task_credits_delta=task_delta,
            contact_cap_delta=contact_delta,
            unit_amount=unit_amount,
            currency=currency or "",
        )

    @staticmethod
    def _active_entitlements(owner, at_time=None):
        model = AddonEntitlementService._get_model()
        qs = model.objects.all()
        qs = qs.for_owner(owner)
        return qs.active(at_time)

    @staticmethod
    def get_uplift(owner, at_time=None) -> AddonUplift:
        entitlements = AddonEntitlementService._active_entitlements(owner, at_time)

        aggregates = entitlements.aggregate(
            task_credits=Coalesce(
                Sum(
                    F("task_credits_delta") * F("quantity"),
                    output_field=IntegerField(),
                ),
                0,
            ),
            contact_cap=Coalesce(
                Sum(
                    F("contact_cap_delta") * F("quantity"),
                    output_field=IntegerField(),
                ),
                0,
            ),
        )

        return AddonUplift(
            task_credits=int(aggregates.get("task_credits", 0) or 0),
            contact_cap=int(aggregates.get("contact_cap", 0) or 0),
        )

    @staticmethod
    def get_task_credit_uplift(owner, at_time=None) -> int:
        return AddonEntitlementService.get_uplift(owner, at_time).task_credits

    @staticmethod
    def get_contact_cap_uplift(owner, at_time=None) -> int:
        return AddonEntitlementService.get_uplift(owner, at_time).contact_cap

    @staticmethod
    def get_price_config(price_id: str, price_data: Mapping[str, Any] | None = None) -> AddonPriceConfig | None:
        return AddonEntitlementService._extract_price_config(price_id, price_data)

    @staticmethod
    def get_active_quantity_for_price(owner, price_id: str, at_time=None) -> int:
        if not price_id:
            return 0
        qs = AddonEntitlementService._active_entitlements(owner, at_time).filter(price_id=price_id)
        try:
            return int(qs.aggregate(total=Coalesce(Sum("quantity"), 0)).get("total") or 0)
        except (TypeError, ValueError, DatabaseError):
            return 0

    @staticmethod
    def get_active_entitlements(owner, price_id: str | None = None, at_time=None):
        qs = AddonEntitlementService._active_entitlements(owner, at_time)
        if price_id:
            qs = qs.filter(price_id=price_id)
        return qs

    @staticmethod
    def get_price_ids(owner_type: str, plan_id: str | None) -> dict[str, list[str]]:
        return AddonEntitlementService._resolve_price_ids(owner_type, plan_id)

    @staticmethod
    def get_price_options(owner_type: str, plan_id: str | None, addon_kind: str | None = None) -> list[AddonPriceConfig]:
        """Return ordered price options for the add-on kind (or all kinds) for the plan/owner."""
        price_lists = AddonEntitlementService._resolve_price_ids(owner_type, plan_id)
        price_map = AddonEntitlementService._build_price_map(plan_id, owner_type)

        ordered_ids: list[str] = []
        if addon_kind:
            ordered_ids.extend(price_lists.get(addon_kind, []))
        else:
            for ids in price_lists.values():
                ordered_ids.extend(ids or [])

        options: list[AddonPriceConfig] = []
        for pid in ordered_ids:
            cfg = price_map.get(pid)
            if cfg:
                options.append(cfg)
        return options

    @staticmethod
    def _build_price_map(plan_id: str | None, owner_type: str) -> dict[str, AddonPriceConfig]:
        """Return relevant add-on price configs for the owner/plan."""
        stripe_settings = get_stripe_settings()
        price_lists = AddonEntitlementService._resolve_price_ids(owner_type, plan_id)
        price_ids: list[str] = []
        for ids in price_lists.values():
            for pid in ids or []:
                if pid not in price_ids:
                    price_ids.append(pid)

        price_map: dict[str, AddonPriceConfig] = {}
        for pid in price_ids:
            if not pid:
                continue
            cfg = AddonEntitlementService._extract_price_config(pid) or AddonPriceConfig(
                price_id=pid,
                product_id="",
                task_credits_delta=0,
                contact_cap_delta=0,
                unit_amount=None,
                currency="",
            )

            # Override task pack delta per plan when using a shared price
            if owner_type == "organization":
                cfg = AddonPriceConfig(
                    price_id=cfg.price_id,
                    product_id=cfg.product_id,
                    task_credits_delta=stripe_settings.task_pack_delta_org_team if cfg.task_credits_delta == 0 else cfg.task_credits_delta,
                    contact_cap_delta=stripe_settings.contact_pack_delta_org_team if cfg.contact_cap_delta == 0 else cfg.contact_cap_delta,
                    unit_amount=cfg.unit_amount,
                    currency=cfg.currency,
                )
            elif plan_id == PlanNames.STARTUP:
                cfg = AddonPriceConfig(
                    price_id=cfg.price_id,
                    product_id=cfg.product_id,
                    task_credits_delta=stripe_settings.task_pack_delta_startup if cfg.task_credits_delta == 0 else cfg.task_credits_delta,
                    contact_cap_delta=stripe_settings.contact_pack_delta_startup if cfg.contact_cap_delta == 0 else cfg.contact_cap_delta,
                    unit_amount=cfg.unit_amount,
                    currency=cfg.currency,
                )
            elif plan_id == PlanNames.SCALE:
                cfg = AddonPriceConfig(
                    price_id=cfg.price_id,
                    product_id=cfg.product_id,
                    task_credits_delta=stripe_settings.task_pack_delta_scale if cfg.task_credits_delta == 0 else cfg.task_credits_delta,
                    contact_cap_delta=stripe_settings.contact_pack_delta_scale if cfg.contact_cap_delta == 0 else cfg.contact_cap_delta,
                    unit_amount=cfg.unit_amount,
                    currency=cfg.currency,
                )
            price_map[pid] = cfg
        return price_map

    @staticmethod
    def get_addon_context_for_owner(owner, owner_type: str, plan_id: str | None) -> dict[str, dict[str, Any]]:
        """Return add-on context keyed by add-on kind for the given owner."""
        price_lists = AddonEntitlementService._resolve_price_ids(owner_type, plan_id)
        if not price_lists:
            return {}

        price_map = AddonEntitlementService._build_price_map(plan_id, owner_type)
        addon_context: dict[str, dict[str, Any]] = {}

        for kind, price_ids in price_lists.items():
            if not price_ids:
                continue

            options: list[dict[str, Any]] = []
            for price_id in price_ids:
                cfg = price_map.get(price_id)
                if not cfg:
                    continue
                entitlements = AddonEntitlementService.get_active_entitlements(owner, price_id)
                expires_at = entitlements.order_by("-expires_at").values_list("expires_at", flat=True).first()
                display_price = ""
                try:
                    if cfg.unit_amount is not None:
                        major = (Decimal(cfg.unit_amount) / Decimal("100")).quantize(Decimal("0.01"))
                        display_price = f"{(cfg.currency or 'USD').upper()} {major}"
                except (InvalidOperation, TypeError):
                    display_price = ""
                options.append(
                    {
                        "price_id": price_id,
                        "product_id": cfg.product_id,
                        "quantity": AddonEntitlementService.get_active_quantity_for_price(owner, price_id),
                        "task_delta": cfg.task_credits_delta,
                        "contact_delta": cfg.contact_cap_delta,
                        "expires_at": expires_at,
                        "unit_amount": cfg.unit_amount,
                        "currency": cfg.currency,
                        "price_display": display_price,
                    }
                )
            if options:
                addon_context[kind] = {
                    "options": options,
                }
                # Preserve backward-compatible accessors for callers that expect a single option.
                addon_context[kind]["price_id"] = options[0]["price_id"]
                addon_context[kind]["product_id"] = options[0]["product_id"]

        return addon_context

    @staticmethod
    def _upsert_task_credit_block(owner, owner_type: str, plan_id: str, entitlement, period_end) -> None:
        """Ensure a TaskCredit block exists for the entitlement period."""
        TaskCredit = apps.get_model("api", "TaskCredit")
        now = timezone.now()
        grant_date = entitlement.starts_at or now
        expiration = period_end or entitlement.expires_at or now
        unique_invoice_ref = f"addon:{entitlement.price_id}:{grant_date.date().isoformat()}"

        filters = {"stripe_invoice_id": unique_invoice_ref, "voided": False}
        if owner_type == "organization":
            filters["organization"] = owner
        else:
            filters["user"] = owner

        credits = entitlement.task_credits_delta * entitlement.quantity
        if credits <= 0:
            return

        obj = TaskCredit.objects.filter(**filters).first()
        update_fields: list[str] = []
        if obj:
            if obj.credits != credits:
                obj.credits = credits
                update_fields.append("credits")
            if obj.expiration_date != expiration:
                obj.expiration_date = expiration
                update_fields.append("expiration_date")
            if obj.granted_date != grant_date:
                obj.granted_date = grant_date
                update_fields.append("granted_date")
            if update_fields:
                obj.save(update_fields=update_fields)
            return

        create_kwargs = dict(
            credits=credits,
            credits_used=0,
            granted_date=grant_date,
            expiration_date=expiration,
            stripe_invoice_id=unique_invoice_ref,
            plan=plan_id or PlanNames.FREE,
            additional_task=False,
            grant_type=GrantTypeChoices.TASK_PACK,
        )
        if owner_type == "organization":
            create_kwargs["organization"] = owner
        else:
            create_kwargs["user"] = owner

        TaskCredit.objects.create(**create_kwargs)

    @staticmethod
    @transaction.atomic
    def sync_subscription_entitlements(
        owner,
        owner_type: str,
        plan_id: str | None,
        subscription_items: Iterable[Mapping[str, Any]],
        period_start,
        period_end,
        created_via: str = "subscription_webhook",
    ) -> None:
        """Align entitlements to match subscription items and refresh TaskCredit blocks."""
        price_map = AddonEntitlementService._build_price_map(plan_id, owner_type)
        if not price_map:
            model = AddonEntitlementService._get_model()
            now = timezone.now()
            stale = model.objects.for_owner(owner).filter(is_recurring=True)
            if stale.exists():
                stale.update(expires_at=now)
            return

        model = AddonEntitlementService._get_model()
        now = timezone.now()
        active_for_owner = model.objects.for_owner(owner).filter(is_recurring=True)
        misconfigured = active_for_owner.exclude(price_id__in=price_map.keys())
        if misconfigured.exists():
            misconfigured.update(expires_at=now)

        active_now = active_for_owner.filter(price_id__in=price_map.keys())

        desired_prices: dict[str, tuple[int, AddonPriceConfig, Mapping[str, Any]]] = {}
        for item in subscription_items or []:
            price = item.get("price") if isinstance(item, Mapping) else None
            price_id = price.get("id") if isinstance(price, Mapping) else None
            if not price_id or price_id not in price_map:
                continue
            try:
                quantity = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0
            if quantity <= 0:
                continue

            cfg = AddonEntitlementService.get_price_config(price_id, price_data=price)
            if not cfg:
                cfg = price_map[price_id]
            desired_prices[price_id] = (quantity, cfg, price or {})

        now = timezone.now()
        seen_price_ids: set[str] = set()
        for price_id, (quantity, cfg, price_obj) in desired_prices.items():
            ent = active_now.filter(price_id=price_id).first()
            starts_at = period_start or now
            ent_expires_at = period_end
            product_id = cfg.product_id or str((price_obj.get("product") or "")) if isinstance(price_obj, Mapping) else ""

            if ent:
                updates: list[str] = []
                if ent.quantity != quantity:
                    ent.quantity = quantity
                    updates.append("quantity")
                if ent.task_credits_delta != cfg.task_credits_delta:
                    ent.task_credits_delta = cfg.task_credits_delta
                    updates.append("task_credits_delta")
                if ent.contact_cap_delta != cfg.contact_cap_delta:
                    ent.contact_cap_delta = cfg.contact_cap_delta
                    updates.append("contact_cap_delta")
                if ent.starts_at != starts_at:
                    ent.starts_at = starts_at
                    updates.append("starts_at")
                if ent.expires_at != ent_expires_at:
                    ent.expires_at = ent_expires_at
                    updates.append("expires_at")
                if ent.product_id != product_id:
                    ent.product_id = product_id
                    updates.append("product_id")
                if not ent.is_recurring:
                    ent.is_recurring = True
                    updates.append("is_recurring")
                if updates:
                    ent.save(update_fields=updates + ["updated_at"])
            else:
                create_kwargs = dict(
                    price_id=price_id,
                    product_id=product_id,
                    quantity=quantity,
                    task_credits_delta=cfg.task_credits_delta,
                    contact_cap_delta=cfg.contact_cap_delta,
                    starts_at=starts_at,
                    expires_at=ent_expires_at,
                    is_recurring=True,
                    created_via=created_via,
                )
                if owner_type == "organization":
                    create_kwargs["organization"] = owner
                else:
                    create_kwargs["user"] = owner
                ent = model.objects.create(**create_kwargs)

            seen_price_ids.add(price_id)
            if cfg.task_credits_delta:
                AddonEntitlementService._upsert_task_credit_block(owner, owner_type, plan_id or PlanNames.FREE, ent, ent_expires_at)

        # Expire entitlements that are no longer present
        stale = (
            active_now.exclude(price_id__in=seen_price_ids)
            if seen_price_ids
            else active_now
        )
        if stale.exists():
            stale.update(expires_at=now)
