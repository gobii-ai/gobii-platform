import time

from util.analytics import Analytics


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
    fbclid = q.get("fbclid")
    if not fbc and fbclid:
        # synthesize per Meta guidance: fb.1.<ts_ms>.<fbclid>
        fbc = f"fb.1.{int(time.time() * 1000)}.{fbclid}"

    rdt_cid = q.get("rdt_cid") or q.get("rdt_click_id")
    ttclid = q.get("ttclid") or q.get("tt_click_id")

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
        },
        "page": {"url": request.build_absolute_uri()},
        # optional feature flag you can pass from caller: context={"consent": True/False}
    }
