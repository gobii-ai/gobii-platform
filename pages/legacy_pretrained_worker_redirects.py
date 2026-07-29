from dataclasses import dataclass

from django.urls import reverse

from pages.legacy_pretrained_worker_database_redirects import (
    LEGACY_DATABASE_TEMPLATE_DESTINATIONS,
)


EXACT_DUPLICATE = "exact_duplicate"
CONTENT_MERGED = "content_merged"
NEW_DESTINATION_REQUIRED = "new_destination_required"


@dataclass(frozen=True)
class LegacyPretrainedWorkerRedirect:
    legacy_slug: str
    destination_category_slug: str
    destination_template_slug: str
    resolution_type: str
    notes: str

    def detail_path(self) -> str:
        return reverse(
            "pages:public_template_detail",
            kwargs={
                "category_slug": self.destination_category_slug,
                "template_slug": self.destination_template_slug,
            },
        )

    def hire_path(self) -> str:
        return reverse(
            "pages:public_template_hire",
            kwargs={
                "category_slug": self.destination_category_slug,
                "template_slug": self.destination_template_slug,
            },
        )

    def launch_path(self) -> str:
        return reverse(
            "pages:public_template_launch",
            kwargs={
                "category_slug": self.destination_category_slug,
                "template_slug": self.destination_template_slug,
            },
        )


LEGACY_PRETRAINED_WORKER_REDIRECTS = {
    redirect.legacy_slug: redirect
    for redirect in (
        LegacyPretrainedWorkerRedirect(
            "competitor-intelligence-analyst",
            "external-intel",
            "competitor-intelligence-analyst",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "vendor-price-analyst",
            "operations",
            "vendor-price-analyst",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "public-safety-scout",
            "risk-compliance",
            "public-safety-scout",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "team-standup-coordinator",
            "team-ops",
            "team-standup-coordinator",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "incident-comms-scribe",
            "operations",
            "incident-comms-scribe",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "sales-pipeline-whisperer",
            "revenue",
            "sales-pipeline-whisperer",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "lead-hunter",
            "sales",
            "b2b-lead-research-agent",
            CONTENT_MERGED,
            "Lead Hunter's prospecting intent and useful workflow details were merged into the richer B2B lead research page.",
        ),
        LegacyPretrainedWorkerRedirect(
            "account-researcher",
            "sales",
            "account-research-agent",
            CONTENT_MERGED,
            "Account Researcher's enrichment intent and useful workflow details were merged into the richer account research page.",
        ),
        LegacyPretrainedWorkerRedirect(
            "talent-scout",
            "recruiting",
            "candidate-sourcing-agent",
            CONTENT_MERGED,
            "Talent Scout's sourcing intent and useful workflow details were merged into the richer candidate sourcing page.",
        ),
        LegacyPretrainedWorkerRedirect(
            "talent-sourcer",
            "recruiting",
            "candidate-sourcing-agent",
            CONTENT_MERGED,
            "This historical slug was an alias for the Talent Scout candidate-sourcing intent.",
        ),
        LegacyPretrainedWorkerRedirect(
            "candidate-researcher",
            "recruiting",
            "candidate-researcher",
            NEW_DESTINATION_REQUIRED,
            "Candidate enrichment is distinct from sourcing, so a differentiated official library page was created.",
        ),
        LegacyPretrainedWorkerRedirect(
            "outreach-agent",
            "sales",
            "outreach-agent",
            NEW_DESTINATION_REQUIRED,
            "Personalized outreach is distinct from lead and candidate research, so a differentiated official library page was created.",
        ),
        LegacyPretrainedWorkerRedirect(
            "employee-onboarding-concierge",
            "people",
            "employee-onboarding-concierge",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "compliance-audit-sentinel",
            "risk-compliance",
            "compliance-audit-sentinel",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "customer-health-monitor",
            "revenue",
            "customer-health-monitor",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "real-estate-research-analyst",
            "research",
            "real-estate-research-analyst",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "project-manager",
            "team-ops",
            "project-manager",
            EXACT_DUPLICATE,
            "The library page uses the same active template record.",
        ),
        LegacyPretrainedWorkerRedirect(
            "account-research-ai-agent",
            "sales",
            "account-research-agent",
            EXACT_DUPLICATE,
            "The old route exposed this newer official template by its internal code.",
        ),
        LegacyPretrainedWorkerRedirect(
            "ai-agent-for-candidate-sourcing",
            "recruiting",
            "candidate-sourcing-agent",
            EXACT_DUPLICATE,
            "The old route exposed this newer official template by its internal code.",
        ),
        LegacyPretrainedWorkerRedirect(
            "b2b-lead-research-agent",
            "sales",
            "b2b-lead-research-agent",
            EXACT_DUPLICATE,
            "The old route exposed this newer official template by its internal code.",
        ),
    )
}

LEGACY_PRETRAINED_WORKER_REDIRECTS.update(
    {
        legacy_slug: LegacyPretrainedWorkerRedirect(
            legacy_slug=legacy_slug,
            destination_category_slug=category_slug,
            destination_template_slug=template_slug,
            resolution_type=EXACT_DUPLICATE,
            notes=(
                "Database-backed template code resolved to this canonical library page "
                "at the retirement cutover."
            ),
        )
        for legacy_slug, (
            category_slug,
            template_slug,
        ) in LEGACY_DATABASE_TEMPLATE_DESTINATIONS.items()
    }
)

RETIRED_LIBRARY_TEMPLATE_REDIRECTS = {
    ("people", "talent-sourcer"): LEGACY_PRETRAINED_WORKER_REDIRECTS["talent-sourcer"],
}


def get_legacy_pretrained_worker_redirect(slug: str | None) -> LegacyPretrainedWorkerRedirect | None:
    normalized_slug = str(slug or "").strip().lower()
    return LEGACY_PRETRAINED_WORKER_REDIRECTS.get(normalized_slug)


def get_retired_library_template_redirect(
    category_slug: str | None,
    template_slug: str | None,
) -> LegacyPretrainedWorkerRedirect | None:
    normalized_route = (
        str(category_slug or "").strip().lower(),
        str(template_slug or "").strip().lower(),
    )
    return RETIRED_LIBRARY_TEMPLATE_REDIRECTS.get(normalized_route)
