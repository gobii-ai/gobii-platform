"""Default code-defined system skill definitions."""

from django.conf import settings

from api.agent.tools.custom_tool_names import CREATE_CUSTOM_TOOL_NAME, CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY
from api.agent.tools.attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
from api.agent.tools.meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY, META_GOBII_TOOL_NAMES
from api.meta_ads_setup import META_ADS_SETUP_INSTRUCTIONS, META_ADS_SETUP_STEPS, META_ADS_TROUBLESHOOTING_TIPS

from .image_generation import IMAGE_GENERATION_PROMPT_INSTRUCTIONS, IMAGE_GENERATION_SYSTEM_SKILL_KEY
from .native_api_cookbooks import render_native_api_cookbook
from .registry import SystemSkillDefinition, SystemSkillDocLink, SystemSkillField


GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY = "google_sheets_native"
APOLLO_NATIVE_SYSTEM_SKILL_KEY = "apollo_native"
HUBSPOT_NATIVE_SYSTEM_SKILL_KEY = "hubspot_native"
DISCORD_NATIVE_SYSTEM_SKILL_KEY = "discord_native"
CODE_WORK_SYSTEM_SKILL_KEY = "code_work"
RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY = "recruitment_sourcing"


def _custom_tool_development_prompt_available(agent) -> bool:
    from api.agent.system_skills.service import get_available_system_skill_tool_names

    return CREATE_CUSTOM_TOOL_NAME in get_available_system_skill_tool_names(agent)


def _format_custom_tool_development_context(agent) -> str:
    from api.agent.tools.custom_tools import format_custom_tools_state_for_prompt

    summary = format_custom_tools_state_for_prompt(agent, recent_limit=3)
    if not summary:
        return ""
    return "Current custom-tool state:\n" + summary


def _app_integrations_url() -> str:
    return f"{str(settings.PUBLIC_SITE_URL or '').strip().rstrip('/')}/app/integrations"


def _native_integration_prompt_context(agent, provider_key: str) -> str:
    from api.services.native_integrations import format_native_integration_permission_prompt
    from api.services.persistent_agent_secrets import resolve_global_secret_owner_for_agent

    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    return format_native_integration_permission_prompt(provider_key, owner_user, owner_org)


def _native_integration_connected(agent, provider_key: str) -> bool:
    from api.services.native_integrations import native_integration_is_connected
    from api.services.persistent_agent_secrets import resolve_global_secret_owner_for_agent

    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    return native_integration_is_connected(provider_key, owner_user, owner_org)


def _google_sheets_native_prompt_context(agent) -> str:
    return _native_integration_prompt_context(agent, "google_drive")


def _apollo_native_prompt_context(agent) -> str:
    return _native_integration_prompt_context(agent, "apollo")


def _hubspot_native_prompt_context(agent) -> str:
    return _native_integration_prompt_context(agent, "hubspot")


def _google_sheets_native_prompt_instructions(agent) -> str:
    integrations_url = _app_integrations_url()
    setup_text = (
        f"If disconnected, send the user to `{integrations_url}` to connect Google Drive and choose allowed sheets; "
        "the completion event wakes you.\n"
        if not _native_integration_connected(agent, "google_drive")
        else ""
    )
    cookbook = render_native_api_cookbook("google_drive")
    return (
        "Use `http_request`; native OAuth covers `sheets.googleapis.com` and `www.googleapis.com/drive`. A concrete "
        "spreadsheet ID is literal and opaque regardless of shape: call Sheets directly; use Drive only for a title or after an inaccessible result. "
        "One complete title search is enough; an empty file list means it is not selected, so give selection guidance instead of broadening or retrying. "
        "Known ID-and-tab value calls need no metadata. For formatting, use one metadata GET, at most one values GET, then one batchUpdate; never vary fields or ranges to reread a successful payload. "
        "Never use web/search results to choose a private sheet. Resolve a tab title and numeric `sheetId` from metadata "
        "instead of guessing. An explicit append request means add a new row; after updated rows/cells are reported, do not "
        "read back or repeat the append unless verification was requested or the result is ambiguous.\n"
        f"{setup_text}"
        f"{cookbook}\n"
        "For a new data sheet with unspecified columns, choose safe obvious defaults, write values, then freeze row 1, "
        "style the header, size columns, format clear dates/numbers, and band rows. For existing formatting, one metadata "
        "inspection is usually enough; avoid duplicate banding; after a successful `batchUpdate`, finish instead of doing "
        "extra readback verification unless requested or ambiguous. Treat `status:error` or non-2xx as failure and repair "
        "before claiming success. If a requested sheet is absent, ask the user to select it in the native integration."
    )


def _apollo_native_prompt_instructions(agent) -> str:
    integrations_url = _app_integrations_url()
    setup_text = (
        f"If disconnected, send the user to `{integrations_url}` to connect Apollo; the completion event wakes you. "
        if not _native_integration_connected(agent, "apollo")
        else ""
    )
    cookbook = render_native_api_cookbook("apollo")
    return (
        "Use `http_request`; native OAuth covers `api.apollo.io` and the documented app.apollo.io profile endpoint. "
        "Test silently: never claim Apollo is connected before a successful call. Bound searches with filters, `page`, "
        "and `per_page`; report remaining pages. Inspect HTTP code and content, not merely tool status.\n"
        f"{setup_text}"
        f"{cookbook}\n"
        "Empty result arrays or a blank match/email are no-result, not integration failure. On any 401/not-connected "
        "response, make no other Apollo or discovery call; send returned reconnect guidance and stop. For 403, stop "
        "retrying and explain possible plan, master API key, or scope limits. For 422, "
        "repair the request shape; treat invalid/unmatched batch entries as a row-level miss and continue valid rows. "
        "Get approval for writes, sequence changes, reveals, or other credit-sensitive work unless already clear. Never "
        "invent a webhook; await an asynchronous `request_id`. Do not use legacy `apollo_io-*`, browser, or web search "
        "when native Apollo can do the work."
    )


def _hubspot_native_prompt_instructions(agent) -> str:
    integrations_url = _app_integrations_url()
    setup_text = (
        f"If disconnected, send the user to `{integrations_url}` to connect HubSpot; the completion event wakes you.\n"
        if not _native_integration_connected(agent, "hubspot")
        else ""
    )
    cookbook = render_native_api_cookbook("hubspot")
    return (
        "Use `http_request`; native OAuth covers `api.hubapi.com`. Test silently: never claim HubSpot is connected "
        "before a successful call.\n"
        f"{setup_text}"
        "Use CRM v3. Bound reads with filters, `limit`, and `after`; report remaining pages.\n"
        f"{cookbook}\n"
        "On any 401/not-connected, make no other HubSpot or discovery call; send returned reconnect guidance and stop. For "
        "writes, deletes, merges, bulk, associations, or lifecycle changes, confirm records, properties, filters, and "
        "effects unless already approved. Prefer native HubSpot over Pipedream, browser/web search, or private tokens."
    )


CODE_WORK_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=CODE_WORK_SYSTEM_SKILL_KEY,
    name="Code Work",
    search_summary="Write, edit, debug, verify, and deploy code with source-of-truth discovery and reviewable changes.",
    tool_names=("read_file", "create_file", "apply_patch", "run_command"),
    enables=(
        "inspect project structure, instructions, and existing code patterns",
        "make small reviewable source, script, config, HTML, CSS, and JavaScript edits",
        "debug failures with concrete commands and evidence",
        "verify changes with tests, builds, syntax checks, smoke checks, or browser/render checks",
        "prepare cautious deployments and rollback context for live software or static sites",
    ),
    use_when=(
        "the user asks to write, edit, modify, fix, debug, refactor, review, test, build, or deploy code",
        "the task touches scripts, source files, configuration, infrastructure files, HTML, CSS, JavaScript, or templates",
        "the task involves a live site or software artifact that needs code changes",
        "the work requires understanding an existing project before changing files",
        "the agent is about to use shell, file-read, file-write, string-replacement, or deployment tools for engineering work",
    ),
    query_aliases=(
        "code",
        "coding",
        "programming",
        "software engineering",
        "developer workflow",
        "edit code",
        "fix code",
        "debug code",
        "refactor",
        "repo",
        "git",
        "tests",
        "frontend",
        "html css",
        "javascript",
        "python",
        "deploy site",
    ),
    prompt_instructions=(
        "Treat code changes as durable engineering artifacts, not one-off text generation.\n"
        "Start by identifying the source of truth. Check project instructions such as AGENTS.md, README, "
        "package/test config, and nearby files. Check whether the workspace is a git repo with commands like "
        "`git rev-parse --show-toplevel` and `git status --short` before assuming there is repo-backed rollback. "
        "If there is no git repo, preserve rollback context for risky edits by keeping a local baseline, backup, "
        "or generated diff before changing important files.\n"
        "Read before writing. Inspect surrounding code, conventions, naming, tests, build scripts, deployment "
        "scripts, and existing helper APIs. Prefer fast targeted discovery such as rg/find/ls/sed/git grep. "
        "Do not infer architecture from filenames alone.\n"
        "Prefer small, reviewable edits. Use patch- or diff-capable editing flows when available. For structured "
        "files, prefer structured parsers when practical: ASTs for code, JSON/YAML parsers for config, and DOM/HTML "
        "parsers for HTML. Avoid whole-file rewrites unless creating a new file, regenerating a deliberately "
        "generated artifact, or replacing a tiny standalone file. Avoid brittle exact-string replacements for large "
        "blocks; if a replacement fails once, inspect the current file before retrying.\n"
        "For repeated transformations, create a named reusable script instead of embedding a long one-off command. "
        "Make transformation scripts idempotent where practical and print a compact summary of files changed, counts, "
        "and validation signals. Do not leave a throwaway script as the only explanation of a complex change.\n"
        "Prove the change with the narrowest meaningful verification first. Use the project's existing commands when "
        "present: targeted tests, typecheck, lint, build, syntax/import checks, smoke commands, local render, curl, "
        "or browser checks. Match verification to risk: HTTP 200 and byte size are not enough for a visual redesign; "
        "use screenshot or browser verification for layout/UI changes when possible. If a check cannot run, state why "
        "and use the best available substitute.\n"
        "Debug by evidence, not guesses. Capture the exact failing command and error, inspect state before retrying, "
        "and if the same class of failure happens twice, stop varying parameters randomly. Re-read docs, list actual "
        "paths/permissions, or ask for the missing fact. Avoid path-variant guessing, guessed web roots, repeated "
        "failed replacements, and routine polling or health checks that do not answer a current question.\n"
        "If git exists, check `git status --short` before edits, avoid overwriting unrelated user changes, and review "
        "`git diff` before finalizing. If git does not exist, make the changed files and verification summary explicit "
        "so the work remains reviewable.\n"
        "Use `$GOBII_SCRATCH_DIR` for temporary build artifacts, downloaded repos, generated intermediates, large dependency "
        "or cache trees, and other non-user-facing work. Scratch files do not sync into agent filespace and may disappear "
        "when sandbox state resets; write user-facing deliverables outside scratch or through filespace-aware tools.\n"
        "When cloning repositories in the sandbox, clone them under `$GOBII_REPO_WORKDIR` with an explicit destination, "
        "for example `git clone <url> $GOBII_REPO_WORKDIR/repo-name`. Do not clone repos directly under `/workspace`; "
        "repo checkout state is durable via pushed branches and PRs, not filespace sync.\n"
        "Deploy only after local verification unless the user explicitly asks for emergency live repair. Before "
        "deploying, know the target host, user, path, and privilege boundary; batch uploads and commands; preserve "
        "the previous live artifact for risky changes; and verify the live result once with checks that match the "
        "change. Do not run routine live health checks after unrelated cron/message events."
    ),
)


IMAGE_GENERATION_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=IMAGE_GENERATION_SYSTEM_SKILL_KEY,
    name="Image Generation",
    search_summary="Generate or edit raster image assets with Gobii's create_image tool.",
    tool_names=("create_image",),
    enables=(
        "generate original raster images from text prompts",
        "edit, restyle, composite, or preserve details from filespace source images",
        "create multiple image assets or variants with distinct prompts and filespace paths",
        "save generated images for messages, documents, attachments, and later edits",
    ),
    use_when=(
        "the user asks to generate a photo, illustration, texture, sprite, mockup, banner, poster, thumbnail, or artwork",
        "the user asks for a new raster logo or brand-mark concept rather than an edit to an existing vector system",
        "the user asks to edit, transform, restyle, composite, or remove the background from an existing raster image",
        "the user needs image-to-image generation that preserves a person, product, logo, layout, text, or visual identity",
        "the user asks for several generated image assets or visual variants",
    ),
    query_aliases=(
        "generate image",
        "create image",
        "make an image",
        "image generation",
        "image edit",
        "edit image",
        "modify image",
        "transform image",
        "image to image",
        "style transfer",
        "transparent background",
        "background removal",
        "product mockup",
        "logo design",
        "poster design",
        "thumbnail design",
        "concept art",
        "illustration",
        "artwork",
    ),
    prompt_instructions=IMAGE_GENERATION_PROMPT_INSTRUCTIONS,
)


CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY,
    name="Custom Tool Development",
    search_summary="Create, patch, and run sandboxed Python custom tools for batch, API, and SQLite workflows.",
    tool_names=(CREATE_CUSTOM_TOOL_NAME,),
    enables=(
        "create or update agent-authored Python tools",
        "batch repeated MCP, API, scraping, validation, and transform work",
        "write durable results directly to the shared agent SQLite database",
        "compose enabled tools from Python with ctx.call_tool",
        "build resumable chunked workflows for slow network or sync jobs",
    ),
    use_when=(
        "the user asks to create a custom tool",
        "work involves repeated tool calls, pagination, fan-out, retries, or backoff",
        "work involves bulk SQLite writes, dedupe, validation, import, export, or sync jobs",
        "intermediate data would otherwise be processed manually in model context",
        "a small deterministic Python tool would make the work faster or more reliable",
    ),
    query_aliases=(
        "custom tool",
        "create custom tool",
        "sandbox tool",
        "python tool",
        "tool development",
        "batch tool",
        "bulk tool",
        "sqlite sync",
        "mcp fanout",
        "api fanout",
    ),
    prompt_instructions=(
        "Use `create_custom_tool` for repeated/fan-out calls, pagination, deterministic transforms, validation/dedupe, or bulk SQLite work.\n"
        "Before creating: exact import `from _gobii_ctx import main`; exact final line `if __name__ == '__main__': main(run)`; "
        "import referenced modules; require real source inputs plus destinations/filters/limits; use "
        "`with ctx.sqlite() as db:`, never `db = ctx.sqlite()`; batch tools return `remaining_work`/`next_cursor`.\n"
        "First call `create_custom_tool(source_path='/tools/my_tool.py', source_code=...)`. Do not pass only `source_path` unless "
        "the file exists. If rejected, fix every issue and retry create_custom_tool, not create_file. Test a small sample, patch the same "
        "file with `apply_patch`, then widen. Define `run(params, ctx)`; PEP 723 lists only third-party dependencies.\n"
        "Mirror each requested variable (tables, destinations, status/date/threshold filters, limits, cursors) in `parameters_schema`, "
        "read it from `params`, and pass it on invocation; never call with empty params unless verified state supplies them. Make slow work resumable with "
        "limits, filters, persisted progress, and cursors. In SQLite set `db.row_factory = sqlite3.Row` before reads; the DB closes after "
        "the context and rows do not support `row.get`. Use `ctx.call_tool` for enabled tools. Final files go under `/workspace` and return "
        "a `$[/exports/...]` path; scratch is temporary. Read secrets from `os.environ`; request missing env vars securely; use SOCKS5-aware "
        "clients with `ctx.requests_proxies()`/`ctx.proxy_url()`.\n"
        "Every result includes `next_action`, status, changed/ready outputs, counts/side effects, skipped items, remaining work/cursor, and "
        "verification. Name ready outputs (`direct_post_urls`, `scrape_ready_urls`, `rows_written`, `records_to_sync`); validators separate "
        "accepted values from rejected inputs with reasons. Save stable workflows as skills."
    ),
    prompt_available=_custom_tool_development_prompt_available,
    prompt_context_renderer=_format_custom_tool_development_context,
)


GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
    name="Google Sheets",
    search_summary="Create, read, update, format, and chart Google Sheets through the native Google Drive integration.",
    tool_names=("http_request",),
    enables=(
        "read Google Sheets metadata and worksheet names",
        "create new Google Sheets spreadsheets",
        "read spreadsheet ranges and rows",
        "append rows to selected spreadsheets",
        "update ranges in selected spreadsheets",
        "format sheets with headers, frozen rows, banding, sizing, and charts",
        "use native Google Drive OAuth with drive.file access",
    ),
    use_when=(
        "the user asks to read a Google Sheet",
        "the user asks to update, append, or write spreadsheet rows",
        "the user asks to create, format, polish, or chart a Google Sheet",
        "the user asks to find or search for one of their Google Sheets by name",
        "the user asks to inspect worksheets, tabs, ranges, cells, or formulas in Google Sheets",
        "the work references a spreadsheet selected through the native Google Drive integration",
    ),
    query_aliases=(
        "google sheets",
        "sheets",
        "spreadsheet",
        "worksheet",
        "google sheet",
        "find my spreadsheet",
        "search my sheets",
        "sheets api",
        "drive file spreadsheet",
    ),
    prompt_instructions_renderer=_google_sheets_native_prompt_instructions,
    prompt_context_renderer=_google_sheets_native_prompt_context,
)


APOLLO_NATIVE_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=APOLLO_NATIVE_SYSTEM_SKILL_KEY,
    name="Apollo",
    search_summary="Use connected Apollo REST APIs for lead sourcing, enrichment, CRM, sequencing, analytics, and sales intelligence.",
    tool_names=("http_request",),
    enables=(
        "search Apollo people and organizations",
        "enrich Apollo people and organizations",
        "work with Apollo accounts, contacts, sequences, tasks, calls, conversations, deals, analytics, and users",
        "use native Apollo OAuth with scoped partner-app access",
        "inspect Apollo usage stats and rate limits",
    ),
    use_when=(
        "the user asks to use Apollo or Apollo.io",
        "the user asks for lead sourcing or prospect search through Apollo",
        "the user asks to enrich people, contacts, accounts, or organizations with Apollo data",
        "the user asks to create, update, or manage Apollo contacts, accounts, sequences, tasks, calls, conversations, or deals",
        "the user asks to check Apollo API usage, rate limits, email accounts, or connected user profile",
        "the work references sales intelligence data available through Apollo",
    ),
    query_aliases=(
        "apollo",
        "apollo.io",
        "apollo api",
        "apollo leads",
        "lead sourcing",
        "lead generation",
        "lead gen",
        "sales prospecting",
        "sales leads",
        "growth sales",
        "prospect search",
        "prospecting",
        "lead lists",
        "account research",
        "buying signal monitoring",
        "people enrichment",
        "contact enrichment",
        "account enrichment",
        "organization enrichment",
        "apollo contacts",
        "apollo accounts",
        "apollo sequences",
        "sales intelligence",
        "usage stats",
        "rate limits",
    ),
    prompt_instructions_renderer=_apollo_native_prompt_instructions,
    prompt_context_renderer=_apollo_native_prompt_context,
)


RECRUITMENT_SOURCING_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY,
    name="Recruitment Sourcing",
    search_summary=(
        "Source, qualify, dedupe, and deliver recruiting candidates while preserving role requirements, "
        "source constraints, and recruiter feedback."
    ),
    tool_names=("search_tools",),
    enables=(
        "intake role requirements and decide when enough information exists to begin sourcing",
        "source candidates across available connected systems, professional networks, spreadsheets, databases, and public web data",
        "qualify candidates against required, preferred, exclusion, geography, compensation, seniority, and work-setup criteria",
        "dedupe candidate pipelines and maintain sourcing status across batches",
        "deliver recruiter-ready candidate tables, CSVs, summaries, and follow-up batches",
    ),
    use_when=(
        "the user asks to source, find, qualify, screen, shortlist, or deliver candidates for a job opening",
        "the user asks for recruiting, talent sourcing, executive search, staffing, lead candidate, or pipeline work",
        "the task references job descriptions, intake notes, test assignments, hiring criteria, recruiters, or candidate delivery",
        "the task requires searching LinkedIn, Apollo, web sources, spreadsheets, databases, or existing candidate ledgers for people",
        "the user gives feedback on sourced candidates and wants the search refined, expanded, or resumed",
    ),
    query_aliases=(
        "recruitment",
        "recruiting",
        "recruiter",
        "talent sourcing",
        "candidate sourcing",
        "candidate search",
        "candidate screening",
        "candidate pipeline",
        "headhunting",
        "executive search",
        "staffing",
        "shortlist candidates",
        "find candidates",
        "source candidates",
        "hiring criteria",
        "job description sourcing",
        "linkedin sourcing",
        "apollo sourcing",
    ),
    prompt_instructions=(
        "Recruitment sourcing means finding candidates worth recruiter review, not filling a quota with keyword "
        "matches. Preserve hard requirements, preferred signals, exclusions, and open questions.\n"
        "Before sourcing, get enough title/responsibility, must-have, seniority, work-setup/location, and dealbreaker detail to screen. "
        "Do not treat phrases like 'start today' as approval when criteria are missing; those phrases only express urgency. Proceed only "
        "after explicit partial-search approval and label assumptions. A failed intake-question call is not a user answer; ask one blocker.\n"
        "Resolve conflicts from hiring-manager signal through intake notes, summary, posting, assignment, then clarification. Do not erase "
        "hard requirements without an explicit change. Use only job-relevant criteria; never filter on protected traits or stereotypes.\n"
        "Choose sources based on the tools and permissions actually available. Prefer structured people/company "
        "sources/ledgers. If a source is unavailable or blocked, use another approved source or say so; never imply it was checked. "
        "Start with one discriminating bounded search; broaden only when its results are insufficient. Never repeat an equivalent query "
        "or profile lookup, and verify each candidate once. For a small first batch, once results supply enough plausible linked candidates, "
        "verify only missing hard filters and deliver; do not broaden for volume. Vary searches only to close a real coverage gap.\n"
        "Verify every recommendation against hard filters: recent role/company, location/setup, relevant experience, source URL, and "
        "dealbreakers. Mark weak/stale/ambiguous evidence low-confidence; never pad counts. For multi-batch work keep a ledger and "
        "dedupe by profile URL first, then normalized name plus company/location; carry recruiter feedback and exclusions forward.\n"
        "Deliver the requested table/CSV with identity, role/company, location, profile URL, confidence, and criterion-linked notes, plus "
        "coverage, exclusions, duplicates, caveats, and remaining work. Outreach, paid-data reveal, or recruiter delivery requires approval "
        "of recipients and side effects. Report valid partial results and the next bounded path. Quality and criteria fidelity beat volume."
    ),
)


HUBSPOT_NATIVE_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=HUBSPOT_NATIVE_SYSTEM_SKILL_KEY,
    name="HubSpot",
    search_summary="Use connected HubSpot REST APIs for contacts, companies, deals, owners, properties, and CRM workflows.",
    tool_names=("http_request",),
    enables=(
        "search HubSpot contacts, companies, and deals",
        "read and update HubSpot CRM records",
        "create HubSpot contacts, companies, and deals after clear user intent",
        "inspect HubSpot owners, properties, and associations",
        "use native HubSpot OAuth with scoped CRM access",
    ),
    use_when=(
        "the user asks to use HubSpot",
        "the user asks to search, read, create, or update HubSpot contacts",
        "the user asks to search, read, create, or update HubSpot companies or deals",
        "the user asks to inspect HubSpot owners, properties, associations, lifecycle stage, pipeline, or CRM data",
        "the work references CRM records available through HubSpot",
    ),
    query_aliases=(
        "hubspot",
        "hubspot api",
        "hubspot crm",
        "hubspot contacts",
        "hubspot companies",
        "hubspot deals",
        "crm contacts",
        "crm companies",
        "crm deals",
        "hubspot owners",
        "hubspot properties",
    ),
    prompt_instructions_renderer=_hubspot_native_prompt_instructions,
    prompt_context_renderer=_hubspot_native_prompt_context,
)


META_ADS_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key="meta_ads_platform",
    name="Meta Ads Platform",
    search_summary="Monitor Meta ad accounts, campaigns, reporting data, and conversion signal quality.",
    tool_names=("meta_ads",),
    enables=(
        "live Meta Ads account health checks",
        "Meta account, campaign, and insights reads",
        "normalized performance reporting across spend, reach, clicks, conversions, CPA, and ROAS",
        "conversion quality checks for Meta Pixel or dataset health",
        "guided onboarding and credential troubleshooting for Meta Ads access",
        "direct SQLite sync of monitoring datasets for follow-up SQL analysis",
    ),
    use_when=(
        "monitor Meta ads performance",
        "check spend, conversions, CPA, ROAS, or campaign health in Meta",
        "track Meta performance over time with durable SQLite baselines",
        "check Meta Pixel or conversion quality health",
        "diagnose Meta Ads access, token, or account setup issues",
        "review Meta Ads account or campaign status before building automations",
    ),
    query_aliases=(
        "meta ads",
        "facebook ads",
        "ads manager",
        "meta ads manager",
        "marketing api",
    ),
    required_profile_fields=(
        SystemSkillField(
            key="META_APP_ID",
            name="App ID",
            description="Meta app identifier.",
            how_to_get=(
                "Register as a Meta developer first, then create a Business app with the Marketing API product. "
                "Copy the App ID from App Settings -> Basic."
            ),
            docs=(
                SystemSkillDocLink(
                    title="Register as a Meta developer",
                    url="https://developers.facebook.com/docs/development/register/",
                ),
                SystemSkillDocLink(
                    title="Create a Meta app",
                    url="https://developers.facebook.com/docs/development/create-an-app/",
                ),
                SystemSkillDocLink(
                    title="Meta app types",
                    url="https://developers.facebook.com/docs/development/create-an-app/app-dashboard/app-types/",
                ),
            ),
        ),
        SystemSkillField(
            key="META_APP_SECRET",
            name="App Secret",
            description="Meta app secret.",
            how_to_get=(
                "Use the same Business app as META_APP_ID. Copy the App Secret from App Settings -> Basic and "
                "rotate it immediately if it is ever exposed."
            ),
            docs=(
                SystemSkillDocLink(
                    title="Meta app settings",
                    url="https://developers.facebook.com/apps/",
                ),
            ),
        ),
        SystemSkillField(
            key="META_SYSTEM_USER_TOKEN",
            name="System User Token",
            description="System user token with ads_read access.",
            how_to_get=(
                "In Business Settings, create a system user, assign the app and ad account to that system user, "
                "then generate a token with ads_read access. Meta may require a different business admin to approve "
                "the token request."
            ),
            docs=(
                SystemSkillDocLink(
                    title="System users overview",
                    url="https://developers.facebook.com/docs/business-management-apis/system-users/",
                ),
                SystemSkillDocLink(
                    title="Generate system user tokens",
                    url="https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/",
                ),
            ),
        ),
        SystemSkillField(
            key="META_AD_ACCOUNT_ID",
            name="Ad Account ID",
            description="Default ad account ID, usually starting with act_.",
            how_to_get=(
                "Copy the ad account ID that the system user can access. If you know only the numeric ID, this setup "
                "screen accepts it and the tool will normalize it to the act_ form."
            ),
            docs=(
                SystemSkillDocLink(
                    title="Marketing API authorization",
                    url="https://developers.facebook.com/docs/marketing-api/get-started/authorization/",
                ),
            ),
        ),
    ),
    optional_profile_fields=(
        SystemSkillField(
            key="META_API_VERSION",
            name="API Version",
            description="Marketing API version override.",
            required=False,
            default="v25.0",
            how_to_get="Optional. Leave blank to use the supported default version.",
        ),
        SystemSkillField(
            key="META_BUSINESS_ID",
            name="Business ID",
            description="Optional business ID for listing owned ad accounts.",
            required=False,
            how_to_get=(
                "Optional. Add this when Meta does not return ad accounts through the default me/adaccounts path "
                "and you want the tool to list owned accounts via the business."
            ),
        ),
        SystemSkillField(
            key="META_DATASET_ID",
            name="Pixel / Dataset ID",
            description="Optional Meta Pixel or dataset ID for conversion-quality monitoring.",
            required=False,
            how_to_get=(
                "Find the Pixel ID in Events Manager. The Meta conversion-quality API uses this as the dataset_id "
                "for monitoring event match quality, deduplication, freshness, and diagnostics."
            ),
            docs=(
                SystemSkillDocLink(
                    title="Conversions API get started",
                    url="https://developers.facebook.com/docs/marketing-api/conversions-api/get-started/",
                ),
                SystemSkillDocLink(
                    title="Dataset Quality API",
                    url="https://developers.facebook.com/docs/marketing-api/conversions-api/dataset-quality-api/",
                ),
            ),
        ),
    ),
    default_values={"META_API_VERSION": "v25.0"},
    setup_instructions=META_ADS_SETUP_INSTRUCTIONS,
    setup_steps=META_ADS_SETUP_STEPS,
    setup_docs=(
        SystemSkillDocLink(
            title="Developer registration",
            url="https://developers.facebook.com/docs/development/register/",
            description="Do this first if developers.facebook.com/apps redirects or the app dashboard never appears.",
        ),
        SystemSkillDocLink(
            title="Create a Meta app",
            url="https://developers.facebook.com/docs/development/create-an-app/",
        ),
        SystemSkillDocLink(
            title="Marketing API authorization",
            url="https://developers.facebook.com/docs/marketing-api/get-started/authorization/",
            description="Confirms the app must be a Business app with Marketing API added.",
        ),
        SystemSkillDocLink(
            title="System users",
            url="https://developers.facebook.com/docs/business-management-apis/system-users/",
        ),
        SystemSkillDocLink(
            title="Generate system user tokens",
            url="https://developers.facebook.com/docs/business-management-apis/system-users/install-apps-and-generate-tokens/",
        ),
        SystemSkillDocLink(
            title="Dataset Quality API",
            url="https://developers.facebook.com/docs/marketing-api/conversions-api/dataset-quality-api/",
            description="Use this when you want production-grade monitoring of event match quality, deduplication, freshness, and diagnostics.",
        ),
    ),
    troubleshooting_tips=META_ADS_TROUBLESHOOTING_TIPS,
)


DISCORD_NATIVE_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=DISCORD_NATIVE_SYSTEM_SKILL_KEY,
    name="Discord",
    search_summary="Provision inbound Discord server/channel subscriptions through the native Gobii bot.",
    tool_names=("discord_channel_subscriptions", "send_discord_message"),
    enables=(
        "receive Discord channel messages through the native Gobii Discord bot",
        "discover Discord guild channels claimed by the agent owner",
        "send Discord replies through Gobii bot webhooks using the agent name and avatar",
        "inspect and disable Discord channel subscriptions",
        "turn selected Discord channels into agent conversations",
    ),
    use_when=(
        "the user wants the agent to receive Discord messages",
        "the user asks to monitor or listen to a Discord channel",
        "the user wants the agent to interact with a Discord server or channel over time",
        "the user wants Discord messages to wake the agent",
        "the user asks whether Discord channel subscriptions are active",
    ),
    query_aliases=(
        "discord",
        "connected app messages",
        "slack receive",
        "slack messages",
    ),
    prompt_instructions=(
        "Use the native Gobii Discord bot tools for Discord setup and replies.\n"
        "When the user asks to connect, set up, enable, or test Discord, immediately call `discord_channel_subscriptions` "
        "with `action=\"list_guilds\"` or `action=\"discover_channels\"`; do not ask whether to start setup first. "
        "Never invent Discord setup links or format separate setup steps yourself; only send URLs returned by the tool.\n"
        "Use `discord_channel_subscriptions` to manage inbound Discord server-channel subscriptions that wake this agent. "
        "V1 supports server channels only. Multiple agents may subscribe to the same guild/channel; each subscribed agent receives inbound channel messages. "
        "Do not set up DMs, all-channel subscriptions, or mention-only routing.\n"
        "Before asking the user for Discord IDs, call `discord_channel_subscriptions` with `action=\"list_guilds\"` or `action=\"discover_channels\"`. "
        "If the tool returns `action_required`, send the returned Gobii Discord `connect_url` as the single setup link. "
        "That link authorizes Discord guild access and installs the Gobii bot in the selected server. "
        "Do not present setup as separate connect and invite steps unless channel discovery later says the bot cannot list channels.\n"
        "After the user says Discord setup is complete, call `list_guilds` or `discover_channels` again. "
        "If the tool returns `selected_guild`, use that server and continue to channel discovery; do not ask the user to choose the server again.\n"
        "After guilds are connected, use `discover_channels` to list channels visible to the Gobii bot. If several channels are returned, ask the user to choose by channel name, "
        "then call `ensure` with the selected `guild_id`, `channel_id`, and `channel_name` so future channel messages wake this agent.\n"
        "Only ask the user for raw server or channel IDs if discovery fails or returns no useful choices. "
        "Do not request Discord server IDs or channel IDs as secrets.\n"
        "Use `send_discord_message` for outbound Discord replies to subscribed channels. Pass `channel_id`, `message`, and the correct `will_continue_work` value. "
        f"To upload files: {SEND_TOOL_ATTACHMENTS_DESCRIPTION} "
        "The backend sends through a channel webhook using the agent's name and avatar.\n"
        "Use `list` before creating duplicates when the current subscription state is unclear. Use `disable` only when the user asks to stop receiving messages from a subscribed channel.\n"
        "If channel discovery says the Gobii bot cannot list channels, send the returned `bot_invite_url` as a fallback repair link and ask the user to install the bot in the target server before retrying discovery."
    ),
)

META_GOBII_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=META_GOBII_SYSTEM_SKILL_KEY,
    name="Meta Gobii",
    search_summary=(
        "Coordinate persistent Gobiis as a control-plane skill, including team management inside the same owner scope."
    ),
    tool_names=META_GOBII_TOOL_NAMES,
    eager_tool_names=(
        "meta_gobii_get_agent_config_options",
        "meta_gobii_list_agents",
        "meta_gobii_request_agent_creation",
    ),
    enables=(
        "list, inspect, create, update, and archive persistent Gobiis",
        "request Gobii creation through the existing human Create/Decline approval flow",
        "configure name, charter, schedule, active state, intelligence tier, daily credit limits, whitelist policy, and proactive opt-in",
        "create, list, update, and remove peer-agent links with message-window limits",
        "send briefings to Gobiis and read or wait on their timelines",
        "upload and list files in a Gobii filespace",
        "manage contacts, allowlists, pending contact requests, contact endpoints, and preferred owner-safe endpoints",
    ),
    use_when=(
        "the user asks to create a team of Gobiis",
        "the user asks to create an entire research team, analyst team, scout team, specialist team, ",
        "or agent-like team for the current Gobii to manage, even without saying Gobii",
        "the user asks to deploy Gobiis or request a specialist Gobii",
        "the user asks to launch specialist agents or make named specialist agent roles real",
        "the user asks to make, create, deploy, prototype, or set up any Gobii, even for one batch or one-off work",
        "the user asks to create, manage, configure, supervise, or restructure Gobiis",
        "the user asks to build or restructure an agent graph",
        "the user asks to audit, rewire, relink, or brief a Gobii graph",
        "the user asks to archive Gobiis or change daily credit limits, resource limits, schedules, or intelligence tiers",
        "the user asks to manage the Gobii graph or control plane",
        "the user asks a Gobii to manage other Gobiis or act as a manager Gobii",
        "the user asks to link Gobiis together and brief them",
        "the user asks to manage persistent Gobii settings, schedules, contacts, allowlists, resource limits, or peer links",
        "the task is explicitly about coordinating multiple Gobiis or maintaining a Gobii team",
    ),
    query_aliases=(
        "meta gobii",
        "meta gobii team manager",
        "manager gobii",
        "team of gobiis",
        "gobii team",
        "agent team",
        "research team",
        "analyst team",
        "specialist team",
        "specialist agent",
        "specialist agents",
        "launch specialist agent",
        "launch specialist agents",
        "lead hunter",
        "growth operator",
        "vendor price analyst",
        "finance ops analyst",
        "project manager agent",
        "chief of staff agent",
        "scout team",
        "create research team",
        "agent graph",
        "gobii graph",
        "gobii control plane",
        "control plane",
        "create agents",
        "manage agents",
        "configure gobiis",
        "supervise gobiis",
        "link agents",
        "brief agents",
        "deploy gobiis",
        "request gobii creation",
        "restructure gobiis",
        "spawn gobiis",
    ),
    prompt_instructions=(
        "Use Meta Gobii only for persistent Gobiis in this same owner or organization scope, not ordinary work that merely mentions Gobii.\n"
        "The common inspect/request tools are loaded first. If another `meta_gobii_*` operation is missing, use `search_tools` "
        "to load that exact operation, then continue.\n"
        "Human approval boundary: before any mutation, summarize the exact agents, links, messages, files, contacts, routes, schedules, "
        "resources, or tiers. Pass user_confirmed=true only after explicit approval. Exception: meta_gobii_request_agent_creation "
        "opens the human Create/Decline flow itself and needs no separate pre-confirmation.\n"
        "For team creation, propose from the user's brief before doing its domain work; the approved Gobiis research after creation. A team means "
        "2–4 complementary linked Gobiis unless the user gives an exact count; never collapse a temporary, "
        "exploratory, audit, demo, or trial team to one. Send one non-duplicated proposal with roles, graph, and one briefing each; ask once. "
        "Then execute only that approved scope. Hard schedule invariant, first match wins: one-off/once/batch/not-standing/no-recurring => none; "
        "explicit cadence/recurring => that cadence; monitor/monitoring/watch/keep-tabs/follow-up (including customer-success churn-risk) => "
        "sensible reversible default; otherwise none. Missing monitoring cadence triggers rule 3's default. Put a new Gobii's approved schedule in "
        "meta_gobii_create_agent; never plan meta_gobii_update_agent merely to attach it. Charter/briefing cadence cannot trigger runs. "
        "Graph restructuring must inspect links and include actual link/unlink mutations, not just narrate them.\n"
        "Use meta_gobii_request_agent_creation for the Create/Decline specialist flow, not legacy spawn_agent. Contacts must be supplied, "
        "approved, or known internal; inspect/set a preferred endpoint for delivery "
        "instead of updating the agent. Grant can_configure only with owner approval. Never expose full email/phone values in user-facing output; "
        "use roles or masked values. Claim inspections/tool runs only from their results; never invent results. Use only provided/created "
        "files, copy them into the target filespace before briefing, and never fetch arbitrary URLs through these tools."
    ),
)


DEFAULT_SYSTEM_SKILL_DEFINITIONS = {
    CODE_WORK_SYSTEM_SKILL.skill_key: CODE_WORK_SYSTEM_SKILL,
    IMAGE_GENERATION_SYSTEM_SKILL.skill_key: IMAGE_GENERATION_SYSTEM_SKILL,
    CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL.skill_key: CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL,
    GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL.skill_key: GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL,
    APOLLO_NATIVE_SYSTEM_SKILL.skill_key: APOLLO_NATIVE_SYSTEM_SKILL,
    RECRUITMENT_SOURCING_SYSTEM_SKILL.skill_key: RECRUITMENT_SOURCING_SYSTEM_SKILL,
    HUBSPOT_NATIVE_SYSTEM_SKILL.skill_key: HUBSPOT_NATIVE_SYSTEM_SKILL,
    META_ADS_SYSTEM_SKILL.skill_key: META_ADS_SYSTEM_SKILL,
    DISCORD_NATIVE_SYSTEM_SKILL.skill_key: DISCORD_NATIVE_SYSTEM_SKILL,
    META_GOBII_SYSTEM_SKILL.skill_key: META_GOBII_SYSTEM_SKILL,
}
