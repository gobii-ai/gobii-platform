import time


def _first_ip(meta):
    xff = meta.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return meta.get("REMOTE_ADDR")


def extract_click_context(request):
    if not request:
        return {}
    q = request.GET
    c = request.COOKIES
    ua = request.META.get("HTTP_USER_AGENT")
    ip = _first_ip(request.META)

    fbp = c.get("_fbp")
    fbc = c.get("_fbc")
    fbclid = q.get("fbclid")
    if not fbc and fbclid:
        # synthesize per Meta guidance: fb.1.<ts>.<fbclid>
        fbc = f"fb.1.{int(time.time())}.{fbclid}"

    rdt_cid = q.get("rdt_cid") or q.get("rdt_click_id")

    utm = {k: v for k, v in q.items() if k.startswith("utm_")}

    return {
        "user_agent": ua,
        "client_ip": ip,
        "utm": utm,
        "click_ids": {"fbp": fbp, "fbc": fbc, "fbclid": fbclid, "rdt_cid": rdt_cid},
        "page": {"url": request.build_absolute_uri()},
        # optional feature flag you can pass from caller: context={"consent": True/False}
    }
