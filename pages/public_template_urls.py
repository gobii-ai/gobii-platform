from django.urls import reverse
from django.utils.text import slugify


UNCATEGORIZED_TEMPLATE_CATEGORY = "Uncategorized"
PUBLIC_TEMPLATE_CATEGORY_SLUG_OVERRIDES = {
    "HR & Recruiting": "recruiting",
}


def _public_template_category_slug_fallback(label: str) -> str:
    return slugify(label) or "uncategorized"


def public_template_category_slug_from_label(category: str | None) -> str:
    label = str(category or "").strip() or UNCATEGORIZED_TEMPLATE_CATEGORY
    return PUBLIC_TEMPLATE_CATEGORY_SLUG_OVERRIDES.get(label) or _public_template_category_slug_fallback(label)


def public_template_category_slug_aliases_from_label(category: str | None) -> tuple[str, ...]:
    label = str(category or "").strip() or UNCATEGORIZED_TEMPLATE_CATEGORY
    canonical_slug = public_template_category_slug_from_label(label)
    fallback_slug = _public_template_category_slug_fallback(label)
    if fallback_slug == canonical_slug:
        return ()
    return (fallback_slug,)


def public_template_category_label(template) -> str:
    category = getattr(template, "normalized_category", None) or getattr(template, "category", None)
    return str(category or "").strip() or UNCATEGORIZED_TEMPLATE_CATEGORY


def public_template_category_slug(template) -> str:
    return public_template_category_slug_from_label(public_template_category_label(template))


def public_template_route_slug(template) -> str:
    return str(getattr(template, "slug", "") or getattr(template, "code", "")).strip()


def public_template_category_path(template) -> str:
    return reverse(
        "pages:library_category",
        kwargs={"category_slug": public_template_category_slug(template)},
    )


def public_template_detail_path(template) -> str:
    return reverse(
        "pages:public_template_detail",
        kwargs={
            "category_slug": public_template_category_slug(template),
            "template_slug": public_template_route_slug(template),
        },
    )


def public_template_hire_path(template) -> str:
    return reverse(
        "pages:public_template_hire",
        kwargs={
            "category_slug": public_template_category_slug(template),
            "template_slug": public_template_route_slug(template),
        },
    )


def public_template_launch_path(template) -> str:
    return reverse(
        "pages:public_template_launch",
        kwargs={
            "category_slug": public_template_category_slug(template),
            "template_slug": public_template_route_slug(template),
        },
    )
