from dataclasses import dataclass
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

INTERNAL_REFERRER_ROOT_DOMAINS = frozenset(
    {
        "gobii.ai",
    }
)

SEARCH_REFERRER_DOMAINS = frozenset(
    {
        "bing.com",
        "duckduckgo.com",
        "google.com",
        "perplexity.ai",
        "yahoo.com",
    }
)

SOCIAL_REFERRER_DOMAINS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "reddit.com",
        "t.co",
        "threads.net",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "youtube.com",
    }
)

SEARCH_UTM_SOURCES = frozenset(
    {
        "bing",
        "duckduckgo",
        "google",
        "perplexity",
        "yahoo",
    }
)

SOCIAL_UTM_SOURCES = frozenset(
    {
        "facebook",
        "fb",
        "instagram",
        "linkedin",
        "meta",
        "reddit",
        "threads",
        "tiktok",
        "twitter",
        "x",
        "youtube",
    }
)

PAID_UTM_MEDIUMS = frozenset(
    {
        "ad",
        "ads",
        "cpc",
        "paid",
        "paid-search",
        "paid_search",
        "paid-social",
        "paid_social",
        "ppc",
        "sem",
    }
)

SOCIAL_UTM_MEDIUMS = frozenset(
    {
        "organic-social",
        "organic_social",
        "paid-social",
        "paid_social",
        "social",
        "social-paid",
        "social_paid",
    }
)


@dataclass(frozen=True)
class SignupSourceAttribution:
    first_meaningful_referrer: str
    signup_source_bucket: str


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


def is_internal_referrer(referrer) -> bool:
    hostname = referrer_hostname(referrer)
    if not hostname:
        return False
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in INTERNAL_REFERRER_ROOT_DOMAINS
    )


def _attr_value(attribution, field: str) -> str:
    return decode_attribution_value(getattr(attribution, field, ""))


def _first_attr_value(attribution, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = _attr_value(attribution, field)
        if value:
            return value
    return ""


def _domain_matches(hostname: str, domains: frozenset[str]) -> bool:
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def _normalize_source_token(source: str) -> str:
    return source.strip().lower().replace(" ", "_")


def _utm_bucket(source: str, medium: str) -> str:
    normalized_source = _normalize_source_token(source)
    normalized_medium = _normalize_source_token(medium)

    is_paid = normalized_medium in PAID_UTM_MEDIUMS or normalized_medium.startswith("paid")
    is_social = normalized_source in SOCIAL_UTM_SOURCES or normalized_medium in SOCIAL_UTM_MEDIUMS
    is_search = normalized_source in SEARCH_UTM_SOURCES

    if is_paid and is_social:
        return "paid_social"
    if is_paid and is_search:
        return "paid_search"
    if is_paid:
        return "paid"
    if is_social:
        return "organic_social"
    if is_search:
        return "organic_search"
    if normalized_medium == "email":
        return "email"
    return "campaign"


def _referrer_bucket(referrer: str) -> str:
    hostname = referrer_hostname(referrer)
    if not hostname:
        return "direct"
    if _domain_matches(hostname, SOCIAL_REFERRER_DOMAINS):
        return "organic_social"
    if _domain_matches(hostname, SEARCH_REFERRER_DOMAINS):
        return "organic_search"
    return "referral"


def resolve_signup_source_attribution(attribution) -> SignupSourceAttribution:
    landing_code = _attr_value(attribution, "landing_code_first")
    if landing_code:
        return SignupSourceAttribution(f"landing:{landing_code}", "landing")

    referrer_code = _attr_value(attribution, "referrer_code")
    if referrer_code:
        return SignupSourceAttribution(f"referral:{referrer_code}", "referral")

    signup_template_code = _attr_value(attribution, "signup_template_code")
    if signup_template_code:
        return SignupSourceAttribution(f"template:{signup_template_code}", "referral")

    if _first_attr_value(attribution, ("gclid_first", "gbraid_first", "wbraid_first")):
        return SignupSourceAttribution("google_ads", "paid_search")

    if _attr_value(attribution, "msclkid_first"):
        return SignupSourceAttribution("microsoft_ads", "paid_search")

    if _first_attr_value(attribution, ("fbclid", "fbc")):
        return SignupSourceAttribution("meta_ads", "paid_social")

    if _attr_value(attribution, "ttclid_first"):
        return SignupSourceAttribution("tiktok_ads", "paid_social")

    if _attr_value(attribution, "rdt_cid_first"):
        return SignupSourceAttribution("reddit_ads", "paid_social")

    utm_source = _attr_value(attribution, "utm_source_first")
    utm_medium = _attr_value(attribution, "utm_medium_first")
    utm_campaign = _attr_value(attribution, "utm_campaign_first")
    if utm_source or utm_medium or utm_campaign:
        meaningful = utm_source or utm_campaign or utm_medium
        return SignupSourceAttribution(meaningful, _utm_bucket(utm_source, utm_medium))

    referrer = clean_acquisition_referrer(_attr_value(attribution, "first_referrer"))
    if referrer and not is_internal_referrer(referrer):
        return SignupSourceAttribution(referrer, _referrer_bucket(referrer))

    return SignupSourceAttribution("direct", "direct")


def first_meaningful_referrer_for_attribution(attribution) -> str:
    return resolve_signup_source_attribution(attribution).first_meaningful_referrer


def signup_source_bucket_for_attribution(attribution) -> str:
    return resolve_signup_source_attribution(attribution).signup_source_bucket
