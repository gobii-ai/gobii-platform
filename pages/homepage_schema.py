from django.conf import settings
from django.templatetags.static import static

WIND_DOWN_CONTACT_EMAIL = "contact@gobii.ai"


def _get_site_url() -> str:
    return settings.PUBLIC_SITE_URL.rstrip("/")


def _schema_absolute_url(path_or_url: str) -> str:
    value = str(path_or_url or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    path = value if value.startswith("/") else f"/{value}"
    return f"{_get_site_url()}{path}"


def _optional_urls(values) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        url = str(value).strip()
        if not url or url in seen:
            continue
        urls.append(url)
        seen.add(url)
    return urls


def build_homepage_structured_data(
    *,
    brand_name: str,
    page_title: str,
    page_description: str,
) -> dict:
    brand_name = str(brand_name or "").strip() or "Gobii"
    site_url = _get_site_url()
    home_url = f"{site_url}/"
    organization_id = f"{site_url}/#organization"
    website_id = f"{site_url}/#website"
    homepage_id = f"{site_url}/#homepage"

    organization = {
        "@type": "Organization",
        "@id": organization_id,
        "name": brand_name,
        "url": home_url,
        "logo": _schema_absolute_url(static("images/gobii_fish_icon_512.png")),
    }
    same_as = _optional_urls(
        [
            settings.PUBLIC_GITHUB_URL,
            settings.PUBLIC_LINKEDIN_URL,
            settings.PUBLIC_HUGGINGFACE_URL,
            settings.PUBLIC_G2_URL,
            settings.PUBLIC_SAASHUB_URL,
            settings.PUBLIC_X_URL,
            settings.PUBLIC_MEDIUM_URL,
        ]
    )
    if same_as:
        organization["sameAs"] = same_as
    organization["contactPoint"] = {
        "@type": "ContactPoint",
        "contactType": "general inquiries",
        "email": WIND_DOWN_CONTACT_EMAIL,
    }

    website = {
        "@type": "WebSite",
        "@id": website_id,
        "name": brand_name,
        "url": home_url,
        "publisher": {"@id": organization_id},
    }
    webpage = {
        "@type": "WebPage",
        "@id": homepage_id,
        "name": page_title,
        "url": home_url,
        "description": page_description,
        "isPartOf": {"@id": website_id},
        "publisher": {"@id": organization_id},
    }

    return {
        "@context": "https://schema.org",
        "@graph": [
            organization,
            website,
            webpage,
        ],
    }
