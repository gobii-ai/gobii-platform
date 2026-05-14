from urllib.parse import unquote, urlsplit


AUTH_REFERRER_DOMAINS = frozenset(
    {
        "accounts.google.com",
        "login.microsoftonline.com",
    }
)

ATTRIBUTION_REFERRER_SESSION_KEYS = (
    "first_referrer",
    "last_referrer",
    "first_path",
    "last_path",
)


def decode_attribution_value(raw_value) -> str:
    if not raw_value:
        return ""
    try:
        decoded = unquote(str(raw_value))
    except ValueError:
        decoded = str(raw_value)
    return decoded.strip().strip('"')


def referrer_hostname(referrer) -> str:
    value = decode_attribution_value(referrer)
    if not value:
        return ""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""

    hostname = parsed.hostname
    if hostname is None and "://" not in value and not value.startswith("//"):
        try:
            hostname = urlsplit(f"//{value}").hostname
        except ValueError:
            hostname = None

    return (hostname or "").strip().lower().rstrip(".")


def is_auth_provider_referrer(referrer) -> bool:
    hostname = referrer_hostname(referrer)
    if not hostname:
        return False
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in AUTH_REFERRER_DOMAINS
    )


def clean_acquisition_referrer(referrer) -> str:
    value = decode_attribution_value(referrer)
    if not value or is_auth_provider_referrer(value):
        return ""
    return value
