from django.conf import settings
from django.templatetags.static import static

HOMEPAGE_SOCIAL_IMAGE_PATH = "images/gobii_og_image_1200x630.png"
HOMEPAGE_SOFTWARE_DESCRIPTION_TEMPLATE = (
    "{brand_name} is an AI agent platform that gives businesses always-on virtual coworkers "
    "capable of browser automation, web research, data collection, and workflow execution."
)
HOMEPAGE_SOFTWARE_DESCRIPTION = HOMEPAGE_SOFTWARE_DESCRIPTION_TEMPLATE.format(brand_name="Gobii")
HOMEPAGE_SOFTWARE_FEATURES = [
    "Virtual coworkers, not chatbots",
    "Every agent has its own computer",
    "A built-in database for every agent",
    "Real output, not just answers",
    "Plugs into your existing stack",
    "Humans and agents, working together",
    "Secure. Self-hostable. Compliance-ready.",
]


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
    software_id = f"{site_url}/#software"

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

    contact_points = []
    contact_email = str(settings.PUBLIC_CONTACT_EMAIL or "").strip()
    support_email = str(settings.PUBLIC_SUPPORT_EMAIL or "").strip()
    if contact_email:
        contact_points.append(
            {
                "@type": "ContactPoint",
                "contactType": "sales",
                "email": contact_email,
            }
        )
    if support_email and support_email != contact_email:
        contact_points.append(
            {
                "@type": "ContactPoint",
                "contactType": "customer support",
                "email": support_email,
            }
        )
    if contact_points:
        organization["contactPoint"] = contact_points

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
        "image": _schema_absolute_url(static(HOMEPAGE_SOCIAL_IMAGE_PATH)),
        "isPartOf": {"@id": website_id},
        "about": {"@id": software_id},
        "mainEntity": {"@id": software_id},
        "publisher": {"@id": organization_id},
        "significantLink": [
            f"{site_url}/teams/",
            f"{site_url}/solutions/",
            f"{site_url}/pricing/",
            f"{site_url}/library/",
            f"{site_url}/comparisons/",
        ],
    }
    software = {
        "@type": "SoftwareApplication",
        "@id": software_id,
        "name": brand_name,
        "url": home_url,
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web",
        "description": HOMEPAGE_SOFTWARE_DESCRIPTION_TEMPLATE.format(brand_name=brand_name),
        "image": _schema_absolute_url(static(HOMEPAGE_SOCIAL_IMAGE_PATH)),
        "featureList": HOMEPAGE_SOFTWARE_FEATURES,
    }

    return {
        "@context": "https://schema.org",
        "@graph": [
            organization,
            website,
            webpage,
            software,
        ],
    }
