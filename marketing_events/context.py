import time

from django.utils import timezone

from util.analytics import Analytics
from marketing_events.telemetry import record_fbc_synthesized


def _client_ip(request):
    """
    Return a trustworthy client IP string or None.

    We rely on Analytics.get_client_ip which already understands Cloudflare /
    Google proxy headers. A value of '0' is treated as "unknown".
    """
    if not request:
        return None
    try:
        ip = Analytics.get_client_ip(request)
    except Exception:
        return None
    if not ip or ip == '0':
        return None
    return ip


def extract_click_context(request):
    if not request:
        return {}
    q = request.GET
    c = request.COOKIES
    ua = request.META.get("HTTP_USER_AGENT")
    ip = _client_ip(request)

    fbp = c.get("_fbp")
    fbc = c.get("_fbc")
    ga_client_id = c.get("_ga")
    fbclid = q.get("fbclid")
    if not fbc and fbclid:
        # synthesize per Meta guidance: fb.1.<ts_ms>.<fbclid>
        fbc = f"fb.1.{int(time.time() * 1000)}.{fbclid}"
        record_fbc_synthesized(source="marketing_events.context.extract_click_context")

    rdt_cid = q.get("rdt_cid") or q.get("rdt_click_id") or c.get("rdt_cid") or c.get("rdt_click_id")
    ttclid = q.get("ttclid") or q.get("tt_click_id")
    li_fat_id = q.get("li_fat_id") or c.get("li_fat_id")

    utm = {k: v for k, v in q.items() if k.startswith("utm_")}

    return {
        "user_agent": ua,
        "client_ip": ip,
        "utm": utm,
        "click_ids": {
            "fbp": fbp,
            "fbc": fbc,
            "fbclid": fbclid,
            "rdt_cid": rdt_cid,
            "ttclid": ttclid,
            "li_fat_id": li_fat_id,
        },
        "ga_client_id": ga_client_id,
        "page": {"url": request.build_absolute_uri()},
        # optional feature flag you can pass from caller: context={"consent": True/False}
    }


def build_marketing_context_from_user(
    user,
    *,
    synthesized_fbc_source: str = "marketing_events.context.build_marketing_context_from_user",
    record_fbc_synthesized_fn=None,
) -> dict[str, object]:
    """Construct marketing context payload from persisted attribution data."""
    context: dict[str, object] = {"consent": True}
    if not user:
        return context
    if record_fbc_synthesized_fn is None:
        record_fbc_synthesized_fn = record_fbc_synthesized

    from api.models import UserAttribution

    try:
        attribution = user.attribution
    except UserAttribution.DoesNotExist:
        return context
    except AttributeError:
        return context

    click_ids: dict[str, str] = {}
    fbc = getattr(attribution, "fbc", "")
    fbclid = getattr(attribution, "fbclid", "")
    fbp = getattr(attribution, "fbp", "")
    rdt_cid = getattr(attribution, "rdt_cid_last", "") or getattr(attribution, "rdt_cid_first", "")
    ttclid = getattr(attribution, "ttclid_last", "") or getattr(attribution, "ttclid_first", "")
    li_fat_id = getattr(attribution, "li_fat_id_last", "") or getattr(attribution, "li_fat_id_first", "")

    if fbc:
        click_ids["fbc"] = fbc
    elif fbclid:
        touch_ts = getattr(attribution, "first_touch_at", None)
        ts_ms = int(touch_ts.timestamp() * 1000) if touch_ts else int(timezone.now().timestamp() * 1000)
        click_ids["fbc"] = f"fb.1.{ts_ms}.{fbclid}"
        record_fbc_synthesized_fn(source=synthesized_fbc_source)
    if fbclid:
        click_ids["fbclid"] = fbclid
    if fbp:
        click_ids["fbp"] = fbp
    if rdt_cid:
        click_ids["rdt_cid"] = rdt_cid
    if ttclid:
        click_ids["ttclid"] = ttclid
    if li_fat_id:
        click_ids["li_fat_id"] = li_fat_id
    if click_ids:
        context["click_ids"] = click_ids

    utm_candidates = {
        "utm_source": getattr(attribution, "utm_source_last", None) or getattr(attribution, "utm_source_first", None),
        "utm_medium": getattr(attribution, "utm_medium_last", None) or getattr(attribution, "utm_medium_first", None),
        "utm_campaign": getattr(attribution, "utm_campaign_last", None) or getattr(attribution, "utm_campaign_first", None),
        "utm_content": getattr(attribution, "utm_content_last", None) or getattr(attribution, "utm_content_first", None),
        "utm_term": getattr(attribution, "utm_term_last", None) or getattr(attribution, "utm_term_first", None),
    }
    utm = {key: value for key, value in utm_candidates.items() if value}
    if utm:
        context["utm"] = utm

    last_client_ip = getattr(attribution, "last_client_ip", None)
    if last_client_ip:
        context["client_ip"] = last_client_ip

    last_user_agent = getattr(attribution, "last_user_agent", None)
    if last_user_agent:
        context["user_agent"] = last_user_agent

    ga_client_id = getattr(attribution, "ga_client_id", None)
    if ga_client_id:
        context["ga_client_id"] = ga_client_id

    return context
