"""Prompt cookbooks for native HTTP API system skills."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeApiRecipe:
    title: str
    method: str
    url: str
    use_when: str
    request_shape: str
    response_shape: str
    guardrails: str


@dataclass(frozen=True)
class NativeApiCookbook:
    provider_key: str
    heading: str
    recipes: tuple[NativeApiRecipe, ...]


def _recipe(
    title: str,
    method: str,
    url: str,
    use_when: str,
    request_shape: str,
    response_shape: str,
    guardrails: str,
) -> NativeApiRecipe:
    return NativeApiRecipe(
        title=title,
        method=method,
        url=url,
        use_when=use_when,
        request_shape=request_shape,
        response_shape=response_shape,
        guardrails=guardrails,
    )


GOOGLE_DRIVE_COOKBOOK = NativeApiCookbook(
    provider_key="google_drive",
    heading="Google Sheets/Drive API cookbook",
    recipes=(
        _recipe(
            title="Search/list accessible spreadsheets",
            method="GET",
            url="https://www.googleapis.com/drive/v3/files",
            use_when="The user gives a sheet title/name, or you need to list selected spreadsheets.",
            request_shape=(
                "Send `fields=files(id,name,mimeType,webViewLink)`, `pageSize=100`, and a complete `q` filter. "
                "Canonical base query: `mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false`; "
                "add `and name contains 'text'` only when known. If you do not know a name term, still send the "
                "complete base query rather than a partial predicate. Put query params in the URL, not headers; "
                "encode quotes as `%27`."
            ),
            response_shape="Use `files[]` entries with `id`, `name`, `mimeType`, and `webViewLink`.",
            guardrails=(
                "Never call partial Drive URLs like `?q=mimeType%3D`, `?q=name%20%3D`, or "
                "`?q=name%20contains%20`; omit the name predicate if unknown. Do not use Drive discovery for a "
                "known spreadsheet ID unless a Sheets endpoint returned missing or inaccessible."
            ),
        ),
        _recipe(
            title="Spreadsheet metadata and tabs",
            method="GET",
            url="https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}",
            use_when="The user gives a spreadsheet ID, asks for worksheets/tabs, or you need sheet IDs before formatting.",
            request_shape="No body. Use the concrete spreadsheet ID directly when supplied.",
            response_shape="Read `spreadsheetId`, `properties.title`, and `sheets[].properties` including `sheetId` and `title`.",
            guardrails=(
                "For a concrete spreadsheet ID, this should be the first call for read, append, update, format, "
                "or chart tasks. Do not search Drive for that ID unless Sheets says the file is missing or inaccessible."
            ),
        ),
        _recipe(
            title="Create a spreadsheet",
            method="POST",
            url="https://sheets.googleapis.com/v4/spreadsheets",
            use_when="The user asks to create a new Google Sheet.",
            request_shape=(
                "POST https://sheets.googleapis.com/v4/spreadsheets with JSON body "
                '`{"properties":{"title":"..."},"sheets":[{"properties":{"title":"Sheet1"}}]}`.'
            ),
            response_shape="Use `spreadsheetId`, `spreadsheetUrl`, and created `sheets[].properties`.",
            guardrails=(
                "Do not use Drive file creation for Google Sheets. Preserve the returned "
                "`sheets[0].properties.sheetId`; it is often not `0`."
            ),
        ),
        _recipe(
            title="Read values",
            method="GET",
            url="https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{url_encoded_range}",
            use_when="The user asks to inspect cells, rows, or a range.",
            request_shape="URL-encode the A1 range.",
            response_shape="Use the returned `range` and `values` ValueRange array.",
            guardrails="If values are absent, treat the range as empty rather than failed unless the API returned an error.",
        ),
        _recipe(
            title="Update values",
            method="PUT",
            url=(
                "https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/"
                "{url_encoded_range}?valueInputOption=USER_ENTERED"
            ),
            use_when="The user asks to replace a known range.",
            request_shape='Send a ValueRange JSON body like `{"values": [[...]]}`.',
            response_shape="Check `updatedRows`, `updatedColumns`, `updatedCells`, and `updatedRange`.",
            guardrails="Use `valueInputOption=USER_ENTERED` for normal user-facing sheet writes.",
        ),
        _recipe(
            title="Append rows",
            method="POST",
            url=(
                "https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/"
                "{url_encoded_range}:append?valueInputOption=USER_ENTERED"
            ),
            use_when="The user asks to add rows below existing data.",
            request_shape='Send a ValueRange JSON body like `{"values": [[...]]}`.',
            response_shape="Check `updates.updatedRows` and `updates.updatedRange`.",
            guardrails="Verify target worksheet/range before appending if duplicate writes would matter.",
        ),
        _recipe(
            title="Format, band, or chart",
            method="POST",
            url="https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}:batchUpdate",
            use_when="The user asks to polish, format, freeze headers, resize columns, add banding, or chart data.",
            request_shape=(
                "Send valid Sheets `requests`. Use `addBanding` with exact key `bandedRange`; bind chart labels "
                "through `basicChart.domains` and numeric values through `basicChart.series`. Use a real numeric "
                "`sheetId` from create response or spreadsheet metadata. For colors, prefer modern style fields: "
                "`backgroundColorStyle.rgbColor`, `firstBandColorStyle.rgbColor`, `secondBandColorStyle.rgbColor`, "
                "and text color under `textFormat.foregroundColorStyle.rgbColor`."
            ),
            response_shape="Inspect `replies[]` and returned IDs such as `bandedRangeId` or chart IDs.",
            guardrails=(
                "Inspect metadata before adding banding to avoid duplicates. If helper rows/columns are hidden for charts, "
                "set `hiddenDimensionStrategy` to `SHOW_ALL`; for `updateChartSpec`, send the complete chart spec and "
                "do not include a `fields` parameter. Never assume `sheetId` is `0`; if a `batchUpdate` returns 400 "
                "after using a guessed sheet ID, GET spreadsheet metadata and retry with the returned `sheetId`, not "
                "by web-searching Sheets docs. If a formatting request returns 400 after using top-level "
                "`foregroundColor` or legacy color keys, retry with the corresponding `*ColorStyle.rgbColor` fields."
            ),
        ),
    ),
)


APOLLO_COOKBOOK = NativeApiCookbook(
    provider_key="apollo",
    heading="Apollo API cookbook",
    recipes=(
        _recipe(
            title="People search",
            method="POST",
            url="https://api.apollo.io/api/v1/mixed_people/api_search",
            use_when="The user asks for net-new prospects or people matching lead criteria.",
            request_shape=(
                "Use JSON body with `page`, `per_page`, and explicit filters. Common array filters: `person_titles`, "
                "`person_seniorities`, `person_locations`, `organization_locations`, `organization_ids`, "
                "`organization_num_employees_ranges` such as `50,200`, and `q_organization_domains_list`; for one "
                "domain in JSON, `q_organization_domains` can be a string like `apollo.io`; in documented query "
                "parameter form, use `q_organization_domains_list[]`. Use "
                "`include_similar_titles=false` for strict title matches."
            ),
            response_shape=(
                "Records are usually under `people`; page state is under `pagination` with `total_entries`. "
                "Use `person.id` and related `organization.id` values for follow-up calls."
            ),
            guardrails=(
                "Use exactly `/mixed_people/api_search`; do not use `/mixed_people/search` or `/mixed_people`. "
                "Validate titles/domains, and do not assume search records include email or phone."
            ),
        ),
        _recipe(
            title="Organization search",
            method="POST",
            url="https://api.apollo.io/api/v1/mixed_companies/search",
            use_when="The user asks for net-new company/account discovery.",
            request_shape=(
                "Use JSON body with `page`, `per_page`, firmographic filters, locations, domains, and employee ranges."
            ),
            response_shape="Records are under `organizations`; use `organization.id` for follow-up calls.",
            guardrails="Use organization search for net-new companies; `/accounts/search` only searches already-added accounts.",
        ),
        _recipe(
            title="People enrichment",
            method="POST",
            url="https://api.apollo.io/api/v1/people/match or /people/bulk_match",
            use_when="The user needs enriched person/company data or revealed email data for known people.",
            request_shape=(
                "For one person, match with email or rich identity details through `/people/match`. For multiple people, "
                "send `/people/bulk_match` with `details` containing at most 10 person objects per request. Include enough "
                "identity data to match reliably, such as email, LinkedIn URL, first/last name plus organization domain or "
                "organization ID. Email-only enrichment can proceed without phone reveal. Phone reveal requires "
                "`reveal_phone_number=true` and an explicit HTTPS `webhook_url`."
            ),
            response_shape=(
                "Single match returns `person`; bulk/asynchronous paths may return `people`, `contacts`, or a `request_id`."
            ),
            guardrails=(
                "A 200 with blank person or missing email is no_match/no_email, not integration failure. Never invent webhook URLs; "
                "wait for webhook payloads when Apollo returns `request_id`. If `/people/bulk_match` returns 400, do not retry "
                "the same malformed batch; fix the payload, reduce to smaller batches or single `/people/match` calls, and "
                "keep each bulk request to 10 people or fewer."
            ),
        ),
        _recipe(
            title="Contact create/update",
            method="POST/PUT",
            url="https://api.apollo.io/api/v1/contacts and /contacts/{contact_id}",
            use_when="The user explicitly approves creating or updating Apollo contacts.",
            request_shape="Validate required fields such as email. Include `run_dedupe=true` when idempotency matters.",
            response_shape="Use `contact.id`, changed fields, and any duplicate/skipped indicators.",
            guardrails="Summarize write scope and side effects before proceeding unless already clearly approved.",
        ),
        _recipe(
            title="Search existing contacts/accounts",
            method="POST",
            url="https://api.apollo.io/api/v1/contacts/search and /accounts/search",
            use_when="The user asks to inspect contacts or accounts already added to the team's Apollo workspace.",
            request_shape=(
                "Use a JSON body with `page`, `per_page`, and optional search filters. Contacts support "
                "`q_keywords`, `contact_stage_ids`, and `contact_label_ids`; accounts support "
                "`q_organization_name`, `account_stage_ids`, and `account_label_ids`."
            ),
            response_shape=(
                "Contact records are under `contacts`; account records are under `accounts`; page state is under "
                "`pagination`."
            ),
            guardrails=(
                "These endpoints search the user's saved Apollo database, not the broader Apollo people/company index. "
                "For net-new discovery, use people or organization search instead."
            ),
        ),
        _recipe(
            title="Sequence search/add contact",
            method="POST",
            url=(
                "https://api.apollo.io/api/v1/emailer_campaigns/search and "
                "GET /email_accounts and "
                "/emailer_campaigns/{sequence_id}/add_contact_ids"
            ),
            use_when="The user asks to find sequences or add contacts to a sequence.",
            request_shape=(
                "Search sequences with `q_name`. Before adding contacts, call `GET https://api.apollo.io/api/v1/email_accounts` "
                "to list linked sending inboxes and choose a valid `send_email_from_email_account_id`. Add contacts with "
                "`emailer_campaign_id`, `contact_ids`, and the selected sending email account ID."
            ),
            response_shape=(
                "Sequences are under `emailer_campaigns`; use `emailer_campaign.id` for writes. Email account records are under "
                "`email_accounts`; use `email_account.id` as `send_email_from_email_account_id`."
            ),
            guardrails=(
                "Do not call `/email_accounts/list`; use `GET /email_accounts` instead. Ask for the sending mailbox if "
                "the available account is ambiguous. HTTP 403 `API_INACCESSIBLE` or 404 may indicate master-key, scope, "
                "endpoint, or plan limitations."
            ),
        ),
        _recipe(
            title="Usage/profile",
            method="POST/GET",
            url=(
                "https://api.apollo.io/api/v1/usage_stats/api_usage_stats and "
                "https://app.apollo.io/api/v1/users/api_profile"
            ),
            use_when="The user asks about Apollo API usage, credits, rate limits, or connected user profile.",
            request_shape="POST the usage-stats endpoint with no body. GET the OAuth profile endpoint with no body.",
            response_shape="Report returned `api_usage_stats`/usage fields, profile fields, and any visible rate/credit limits.",
            guardrails=(
                "Do not call the obsolete `/usage_stats` path or other obsolete usage endpoints such as "
                "`/credit_usage` or `/auth/credit_usage_stats`; use `/usage_stats/api_usage_stats` instead. "
                "Use the app.apollo.io host only for documented profile/OAuth metadata endpoints."
            ),
        ),
    ),
)


HUBSPOT_COOKBOOK = NativeApiCookbook(
    provider_key="hubspot",
    heading="HubSpot CRM v3 API cookbook",
    recipes=(
        _recipe(
            title="Search contacts, companies, or deals",
            method="POST",
            url="https://api.hubapi.com/crm/v3/objects/{objectType}/search",
            use_when="The user asks to find CRM contacts, companies, or deals by known properties.",
            request_shape=(
                "Use `/crm/v3/objects/contacts/search`, `/crm/v3/objects/companies/search`, or "
                "`/crm/v3/objects/deals/search`. Body includes `filterGroups` with `filters` of `propertyName`, "
                "`operator`, and `value`; include explicit `properties`, `limit`, and `after` pagination when continuing."
            ),
            response_shape="Read `results[]`, `total`, and `paging.next.after`; object fields are under `properties`.",
            guardrails="Keep searches bounded and report when `paging.next.after` means more pages remain.",
        ),
        _recipe(
            title="Read/create/update CRM object",
            method="GET/POST/PATCH",
            url="https://api.hubapi.com/crm/v3/objects/{objectType} and /{objectType}/{objectId}",
            use_when="The user asks to read, create, or update contacts, companies, or deals.",
            request_shape="For writes, send `properties` with exact HubSpot property names; for reads, request explicit `properties`.",
            response_shape="Use returned `id`, `properties`, `createdAt`, `updatedAt`, and `archived`.",
            guardrails=(
                "For creates, updates, deletes, merges, lifecycle-stage changes, and bulk changes, summarize "
                "records, properties, filters, and side effects before proceeding unless already approved."
            ),
        ),
        _recipe(
            title="Owners",
            method="GET",
            url="https://api.hubapi.com/crm/v3/owners/",
            use_when="The user asks who owns a CRM record or you need owner IDs for assignment.",
            request_shape="No body; paginate if HubSpot returns paging.",
            response_shape="Use `results[]` with owner IDs, names, email, and archived state.",
            guardrails="Do not guess owner IDs from names; read owners when assignment matters.",
        ),
        _recipe(
            title="Properties",
            method="GET",
            url="https://api.hubapi.com/crm/v3/properties/{objectType}",
            use_when="You need valid property names, options, lifecycle-stage values, or field metadata.",
            request_shape="No body. Use object types such as `contacts`, `companies`, or `deals`.",
            response_shape="Use `results[]` property definitions including `name`, `label`, `type`, `fieldType`, and `options`.",
            guardrails="Read properties before writing unfamiliar fields instead of inventing property names.",
        ),
        _recipe(
            title="Associations",
            method="GET/PUT",
            url=(
                "https://api.hubapi.com/crm/v3/objects/{fromObjectType}/{fromObjectId}/associations/"
                "{toObjectType} and /{toObjectType}/{toObjectId}/{associationType}"
            ),
            use_when="The user asks for relationships between CRM records or explicitly approves association changes.",
            request_shape=(
                "For reads, use the source object type/ID and target object type. For writes, append the target object "
                "ID and association type: `/{toObjectType}/{toObjectId}/{associationType}`."
            ),
            response_shape="Read associated object IDs and association metadata from returned results.",
            guardrails="Work with associations only when requested; association writes are side-effecting and need clear approval.",
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

    lines = [f"API cookbook: {cookbook.heading}"]
    for recipe in cookbook.recipes:
        lines.extend(
            [
                f"- {recipe.title}: {recipe.method} {recipe.url}",
                f"  Use when: {recipe.use_when}",
                f"  Request: {recipe.request_shape}",
                f"  Response: {recipe.response_shape}",
                f"  Guardrails: {recipe.guardrails}",
            ]
        )
    return "\n".join(lines)
