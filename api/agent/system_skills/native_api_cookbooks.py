"""Compact endpoint maps for native HTTP API system skills."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeApiCookbook:
    provider_key: str
    heading: str
    recipes: tuple[str, ...]


GOOGLE_DRIVE_COOKBOOK = NativeApiCookbook(
    provider_key="google_drive",
    heading="Google Sheets/Drive API cookbook",
    recipes=(
        (
            "Find by name: GET https://www.googleapis.com/drive/v3/files. Request: URL query "
            "`q=mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false` (optionally "
            "`and name contains 'text'`), `fields=files(id,name,mimeType,webViewLink)`, `pageSize=100`; "
            "encode quotes as `%27`. Response: `files[]`. Guardrails: use a complete `q`, never a partial "
            "predicate; do not discover a known spreadsheet ID unless Sheets reports it inaccessible."
        ),
        (
            "Metadata/create: GET/POST https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}. "
            "Request: omit `{spreadsheetId}` only for create; creation body may include title and initial tab. "
            "Response: preserve returned `spreadsheetId`, tab title, and numeric `sheetId`. Guardrails: never "
            "assume `Sheet1` or sheet ID `0`; do not use `/v1`, Drive file creation, or GET the collection to list sheets."
        ),
        (
            "Values: GET/PUT https://sheets.googleapis.com/v4/spreadsheets/{id}/values/{encodedA1} or POST "
            "`.../{encodedA1}:append?valueInputOption=USER_ENTERED`. Request: writes use "
            "`{\"values\":[[...]]}` and `valueInputOption=USER_ENTERED`. Response: inspect updated rows/cells/range; "
            "missing values means empty. Guardrails: an explicit append always adds a row; after an unambiguous "
            "success, do not repeat it or read back unless verification was requested. Write a small user-supplied table "
            "directly; do not stage or restage it in SQLite."
        ),
        (
            "Format/chart: POST https://sheets.googleapis.com/v4/spreadsheets/{id}:batchUpdate. Request: valid "
            "`requests`; `addBanding` uses exact key `bandedRange` and modern `*ColorStyle.rgbColor`; chart labels "
            "use `basicChart.domains`, numbers use `basicChart.series`, and hidden helpers require "
            "`chart.spec.hiddenDimensionStrategy=SHOW_ALL` beside `basicChart`. Guardrails: inspect metadata to avoid duplicate banding; "
            "`updateChartSpec` sends the complete spec without `fields`."
        ),
    ),
)


APOLLO_COOKBOOK = NativeApiCookbook(
    provider_key="apollo",
    heading="Apollo API cookbook",
    recipes=(
        (
            "People search: POST https://api.apollo.io/api/v1/mixed_people/api_search (keep `api_`). Body: integer `page`/`per_page`; arrays `person_titles`, "
            "`person_locations` (where the person lives), `organization_locations` (employer HQ); string `q_keywords`. Use only the location dimension requested. Never invent singular/"
            "industry keys or comma-join arrays. Response: `people[]`, `pagination`, and person `id`; search does not "
            "reveal email/phone. Never use `/mixed_people` or `/mixed_people/search`."
        ),
        (
            "Companies/workspace: POST `/mixed_companies/search` for net-new `organizations[]`; POST "
            "`/contacts/search` or `/accounts/search` only for saved records. Request: filters plus `page`/`per_page`. "
            "Response: inspect the matching array and `pagination`."
        ),
        (
            "Enrichment: POST `/people/match` for one person; use `/people/bulk_match` only for 2-10 people. Request: email/LinkedIn URL or name plus "
            "company; pass the returned person `id`, do not invent legacy keys such as `personId`; bulk uses `details[]`. "
            "Response: `person`, `people`, or `request_id`. Guardrails: blank/missing "
            "emails are row-level misses; on 400 or 422 do not retry the same malformed batch. Phone reveal needs "
            "`reveal_phone_number=true` and an explicit HTTPS `webhook_url`."
        ),
        (
            "Contacts/sequences: POST `/contacts` (or PUT `/contacts/{id}`); sequence search/add uses "
            "`/emailer_campaigns/search` and `/emailer_campaigns/{id}/add_contact_ids`. Get senders with "
            "`GET /email_accounts`; Do not call `/email_accounts/list`. Writes and credit-sensitive reveals need "
            "clear scope/approval."
        ),
        (
            "Usage/profile: POST `/usage_stats/api_usage_stats`; GET "
            "https://app.apollo.io/api/v1/users/api_profile. Guardrails: never use obsolete `/usage_stats`, "
            "`/credit_usage`, or `/auth/credit_usage_stats`; app.apollo.io is only for documented OAuth metadata."
        ),
    ),
)


HUBSPOT_COOKBOOK = NativeApiCookbook(
    provider_key="hubspot",
    heading="HubSpot CRM v3 API cookbook",
    recipes=(
        (
            "Search: POST https://api.hubapi.com/crm/v3/objects/{contacts|companies|deals}/search. Request: "
            "`filterGroups.filters` with `propertyName`, `operator`, `value`; request explicit `properties`, "
            "`limit`, and `after`. Response: `results[]`, `total`, `paging.next.after`."
        ),
        (
            "Objects: GET/POST/PATCH `/crm/v3/objects/{type}` or `/{type}/{id}`. Request: writes put exact "
            "HubSpot names under `properties`. Response: use returned `id`, properties, timestamps, and `archived`. "
            "Guardrails: an approved exact ID/property/value update goes straight to PATCH; do not pre-read or read "
            "back unless required information is missing, the result is ambiguous, or verification was requested."
        ),
        (
            "Schema/owners/links: GET `/crm/v3/properties/{type}` before unfamiliar fields; GET "
            "`/crm/v3/owners/` rather than guessing IDs; associations use `/crm/v3/objects/{from}/{id}/associations/{to}` "
            "(append `/{targetId}/{associationType}` only for an approved write)."
        ),
    ),
)


NATIVE_API_COOKBOOKS = {
    GOOGLE_DRIVE_COOKBOOK.provider_key: GOOGLE_DRIVE_COOKBOOK,
    "google_sheets": GOOGLE_DRIVE_COOKBOOK,
    APOLLO_COOKBOOK.provider_key: APOLLO_COOKBOOK,
    HUBSPOT_COOKBOOK.provider_key: HUBSPOT_COOKBOOK,
}


def render_native_api_cookbook(provider_key: str) -> str:
    cookbook = NATIVE_API_COOKBOOKS.get(str(provider_key or "").strip().lower())
    if cookbook is None:
        return ""
    return "\n".join(
        [f"API cookbook: {cookbook.heading}", *[f"- {recipe}" for recipe in cookbook.recipes]]
    )
