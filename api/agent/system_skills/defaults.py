"""Default code-defined system skill definitions."""

from django.conf import settings

from api.agent.tools.custom_tool_names import CREATE_CUSTOM_TOOL_NAME, CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY
from api.agent.tools.attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
from api.agent.tools.meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY, META_GOBII_TOOL_NAMES
from api.agent.tools.secure_api_request import (
    SECURE_API_REQUEST_TOOL_NAME,
    SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL_KEY,
)
from api.meta_ads_setup import META_ADS_SETUP_INSTRUCTIONS, META_ADS_SETUP_STEPS, META_ADS_TROUBLESHOOTING_TIPS

from .image_generation import IMAGE_GENERATION_PROMPT_INSTRUCTIONS, IMAGE_GENERATION_SYSTEM_SKILL_KEY
from .native_api_cookbooks import render_native_api_cookbook
from .registry import SystemSkillDefinition, SystemSkillDocLink, SystemSkillField


GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY = "google_sheets_native"
APOLLO_NATIVE_SYSTEM_SKILL_KEY = "apollo_native"
HUBSPOT_NATIVE_SYSTEM_SKILL_KEY = "hubspot_native"
DISCORD_NATIVE_SYSTEM_SKILL_KEY = "discord_native"
WEBHOOKS_SYSTEM_SKILL_KEY = "webhooks"
CODE_WORK_SYSTEM_SKILL_KEY = "code_work"
RECRUITMENT_SOURCING_SYSTEM_SKILL_KEY = "recruitment_sourcing"
COMPUTER_SYSTEM_SKILL_KEY = "computer"


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


def _computer_prompt_available(agent) -> bool:
    from api.models import ComputerDeviceAssignment
    from api.services.computer_relay import computer_cpp_enabled_for_user

    owners = {
        assignment.device.owner
        for assignment in ComputerDeviceAssignment.objects.filter(
            agent=agent,
            revoked_at__isnull=True,
            device__revoked_at__isnull=True,
        ).select_related("device__owner")
    }
    return any(computer_cpp_enabled_for_user(owner) for owner in owners or {agent.user})


def _computer_prompt_context(agent) -> str:
    from api.models import ComputerDeviceAssignment
    from api.services.computer_relay import computer_cpp_enabled_for_user, get_device_presence

    assignments = (
        ComputerDeviceAssignment.objects.filter(
            agent=agent,
            revoked_at__isnull=True,
            device__revoked_at__isnull=True,
        )
        .select_related("device__owner")
        .prefetch_related("device__apps")
    )
    lines = []
    for assignment in assignments:
        device = assignment.device
        if not computer_cpp_enabled_for_user(device.owner):
            continue
        if device.is_paused:
            state = "paused"
        elif get_device_presence(device.id):
            state = "online"
        else:
            state = "offline"
        approved_apps = [
            app.display_name
            for app in device.apps.all()
            if app.approval_state == app.ApprovalState.APPROVED
            and app.is_available
            and app.approved_schema_hash == app.reported_schema_hash
        ]
        lines.append(
            f"- {device.display_name}: {state}; apps={', '.join(approved_apps) if approved_apps else 'none approved'}"
        )
    if not lines:
        return "Connected computer state: none."
    return "Connected computer state:\n" + "\n".join(lines)


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


def _native_connection_gate(agent, provider_key: str, provider_name: str, setup_action: str) -> str:
    if _native_integration_connected(agent, provider_key):
        return ""
    return (
        f"Current state: {provider_name} is not connected. Do not call `http_request`, `search_tools`, legacy tools, "
        f"or browser automation for {provider_name} while it is disconnected. Tell the current requester in this "
        f"conversation to open `{_app_integrations_url()}` and {setup_action}. Park this work until the native "
        "connection event wakes you; continue unrelated work if useful."
    )


def _google_sheets_native_prompt_context(agent) -> str:
    return _native_integration_prompt_context(agent, "google_drive")


def _apollo_native_prompt_context(agent) -> str:
    return _native_integration_prompt_context(agent, "apollo")


def _hubspot_native_prompt_context(agent) -> str:
    return _native_integration_prompt_context(agent, "hubspot")


def _webhooks_prompt_context(agent) -> str:
    def triggered(hook):
        return hook.last_triggered_at.isoformat() if hook.last_triggered_at else "never"

    inbound = [
        f"- {hook.name} (id={hook.id}, {'active' if hook.is_active else 'inactive'}, last triggered={triggered(hook)})"
        for hook in agent.inbound_webhooks.order_by("name")
    ]
    outbound = [
        f"- {hook.name} (id={hook.id}, last sent={triggered(hook)}, last status="
        f"{hook.last_response_status if hook.last_response_status is not None else 'none'})"
        for hook in agent.webhooks.order_by("name")
    ]
    return "\n".join([
        "Current native Gobii webhook configuration:",
        "Inbound triggers:",
        *(inbound or ["- None configured"]),
        "Outbound destinations:",
        *(outbound or ["- None configured"]),
        "Endpoint and destination URLs are intentionally omitted. Use the matching management tool with action=get only when needed.",
    ])


def _google_sheets_native_prompt_instructions(agent) -> str:
    connection_gate = _native_connection_gate(
        agent,
        "google_drive",
        "Google Drive",
        "connect Google Drive, then choose the spreadsheets I may access",
    )
    if connection_gate:
        return connection_gate
    missing_file_text = (
        "If the requested spreadsheet is not listed, ask the user to choose it through the Google Drive native "
        "integration before making Sheets API calls for that file."
    )
    cookbook = render_native_api_cookbook("google_drive")
    return (
        "Use `http_request` for Google Sheets and Drive API calls. Native Google Drive OAuth is applied "
        "automatically for `https://sheets.googleapis.com/` and `https://www.googleapis.com/drive/` requests.\n"
        "Choose the API from what the user supplied:\n"
        "- Spreadsheet ID: call a Sheets endpoint first, using that exact ID. An ID is any opaque token supplied as "
        "the spreadsheet ID or found after `/d/` in a Sheets URL; it does not need to look familiar or real. Do not "
        "search Drive for it unless Sheets reports missing or inaccessible.\n"
        "- Spreadsheet title/name: search connected Drive files with one complete `q` filter. Do not send partial "
        "filters such as only `q=mimeType=` or `q=name contains`; omit the name predicate if necessary.\n"
        "This integration uses Google `drive.file`, so an inaccessible spreadsheet may need user selection in Google Picker. "
        "Put `fields`, `pageSize`, and `q` in the request URL query string, never in `headers` or `headers.params`; "
        "percent-encode quotes in `q` as `%27`. "
        "There is no Sheets API endpoint for listing spreadsheets: never call `GET https://sheets.googleapis.com/v4/spreadsheets`; "
        "use Drive `GET https://www.googleapis.com/drive/v3/files` with a spreadsheet MIME-type query instead. "
        "Use Sheets API v4 for spreadsheet operations, including creation with `POST https://sheets.googleapis.com/v4/spreadsheets`; "
        "do not use `/v1/spreadsheets`. "
        "Do not assume a tab is named `Sheet1`; fetch spreadsheet metadata and use the returned `sheets[].properties.title` "
        "before reading or writing a guessed tab. "
        "Do not use web search, `search_tools`, or public `docs.google.com` results to choose a private sheet.\n"
        f"{cookbook}\n"
        "When creating a new data spreadsheet, complete these calls in order: (1) POST to create it, (2) PUT the "
        "values using the returned `spreadsheetId`, then (3) POST formatting to that ID's `:batchUpdate` endpoint. "
        "Do not read the values between those calls. If columns were not specified, choose safe, obvious defaults. "
        "The baseline format freezes row 1, bolds and colors the header, auto-resizes populated columns, applies "
        "sensible number/date formats when column meaning is clear, and adds alternating row colors with "
        "`addBanding` using the exact key `bandedRange`.\n"
        "Formatting and charts use `POST https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}:batchUpdate` "
        "with a JSON `requests` array; never put formatting requests under a `/values/...` URL. Do not mix legacy "
        "color fields such as `backgroundColor`/`foregroundColor` "
        "with `backgroundColorStyle`/`foregroundColorStyle` in the same cell format or banded range. Prefer the "
        "modern `*ColorStyle.rgbColor` fields described in the cookbook unless you must preserve a legacy format.\n"
        "Before adding banding to an existing sheet, inspect spreadsheet metadata. If a matching banded range "
        "already exists, skip `addBanding` or update/delete the existing banded range instead of adding a duplicate. "
        "For known-ID formatting tasks, one metadata inspection is usually enough; after a successful `batchUpdate` "
        "that satisfies the request, send the final response instead of doing extra readback verification unless the "
        "user asked for verification or the API result is ambiguous. "
        "Malformed `batchUpdate` requests usually need the request object names fixed, not blind retries.\n"
        "For charts, bind labels through `basicChart.domains` and numeric values through `basicChart.series`. If "
        "you add helper columns or rows for numeric data and hide them, set `hiddenDimensionStrategy` to `SHOW_ALL`; "
        "otherwise the chart may show no series. For `updateChartSpec`, send the complete chart spec and do not "
        "include a `fields` parameter.\n"
        "For native API calls, treat a tool result with `status: error` or a non-2xx `status_code` as a failed API "
        "call. Use the returned guidance and response body to repair the request before telling the user it worked.\n"
        f"{missing_file_text}"
    )


def _apollo_native_prompt_instructions(agent) -> str:
    connection_gate = _native_connection_gate(
        agent,
        "apollo",
        "Apollo",
        "connect Apollo",
    )
    if connection_gate:
        return connection_gate
    cookbook = render_native_api_cookbook("apollo")
    return (
        "Use `http_request` for Apollo REST API calls. Native Apollo OAuth is applied automatically for "
        "`https://api.apollo.io/` requests and the Apollo profile endpoint "
        "`https://app.apollo.io/api/v1/users/api_profile`.\n"
        "Use `https://api.apollo.io/api/v1/...` for Apollo API work unless a documented OAuth metadata endpoint "
        "specifically uses `https://app.apollo.io/api/v1/...`.\n"
        "Use documented Apollo endpoints exactly. For people search, use `/mixed_people/api_search`; "
        "do not use `/mixed_people/search` or `/mixed_people`. For usage, use `/usage_stats/api_usage_stats`, not "
        "`/usage_stats`, `/credit_usage`, or `/auth/credit_usage_stats`. For linked sending inboxes, use "
        "`GET /email_accounts`, not `/email_accounts/list`.\n"
        "Use bounded requests with explicit filters plus `page` and `per_page`; avoid broad unbounded exports or "
        "searches, and report when more pages remain. Inspect both `status_code` and response `content`: "
        "`http_request` status `ok` only means the HTTP request completed, not that Apollo returned useful data.\n"
        f"{cookbook}\n"
        "Classify Apollo outcomes by the actual response: useful nonempty output, connect/reconnect required, "
        "invalid credentials, missing scopes or API-inaccessible plan/master-key limitation, no results/no email, "
        "validation error, or partial side-effect failure. A 200 with an empty `people`, `contacts`, `accounts`, "
        "`organizations`, or `emailer_campaigns` array is a no-result response unless Apollo includes an explicit "
        "error. A 200 `/people/match` response with a blank person object or missing email is no_match/no_email, "
        "not an integration failure. For 401, ask the user to reconnect Apollo; for 403, stop retrying and explain "
        "that the connected Apollo account may lack the required plan, master API key, or scope. For 422, repair "
        "the request shape before retrying; in per-person enrichment batches, treat one invalid or unmatched person "
        "as a row-level miss and continue with the remaining valid people when possible.\n"
        "For write-heavy, sequence-changing, contact/account creation, phone reveal, personal email reveal, "
        "waterfall enrichment, or other credit-sensitive operations, summarize scope, filters, side effects, "
        "and credit/plan sensitivity before proceeding unless the user has already clearly approved that operation.\n"
        "Never invent webhook URLs. For phone reveal, personal-email reveal, or webhook-based enrichment, use only "
        "an explicitly configured HTTPS webhook URL or ask the user for one. Phone reveal uses "
        "`reveal_phone_number=true` and requires `webhook_url`; email-only enrichment should still proceed without "
        "phone reveal. If Apollo returns a `request_id` for asynchronous enrichment, wait for the webhook payload "
        "to be delivered to the configured webhook URL. Do not use legacy `apollo_io-*` tools, "
        "browser automation, or web search when the connected native Apollo API can do the work."
    )


def _hubspot_native_prompt_instructions(agent) -> str:
    connection_gate = _native_connection_gate(
        agent,
        "hubspot",
        "HubSpot",
        "connect HubSpot",
    )
    if connection_gate:
        return connection_gate
    cookbook = render_native_api_cookbook("hubspot")
    return (
        "Use `http_request` for HubSpot REST API calls. Native HubSpot OAuth is applied automatically for "
        "`https://api.hubapi.com/` requests.\n"
        "Use HubSpot CRM v3 endpoints for core CRM work. Keep requests bounded with explicit filters, "
        "`limit`, and `after` pagination where applicable; report when more pages remain.\n"
        f"{cookbook}\n"
        "For creates, updates, deletes, merges, bulk changes, association changes, lifecycle-stage changes, "
        "or other side-effecting operations, summarize the exact records, properties, filters, and side effects "
        "before proceeding unless the user has already clearly approved that operation.\n"
        "Do not use Pipedream HubSpot tools, browser automation, web search, or manually supplied private-app "
        "tokens when the connected native HubSpot API can do the work."
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
        "Use a custom tool for repeated API/MCP calls, pagination, transforms, validation, syncs, or bulk SQLite "
        "work. Keep the development loop in this order:\n"
        "1. Call create_custom_tool once with source_path, source_code, and a schema for real runtime inputs.\n"
        "2. Invoke the returned custom_* tool with a small, concrete sample.\n"
        "3. If runtime fails, apply_patch to that same source file and invoke custom_* again. Do not call "
        "create_custom_tool again after successful registration. Retry creation only when creation was rejected.\n"
        "Source must import `from _gobii_ctx import main`, define `run(params, ctx)`, and end with "
        "`if __name__ == '__main__': main(run)`. Add PEP 723 third-party dependencies and import every referenced "
        "module. Use `ctx.call_tool(name, params)` for enabled tools.\n"
        "For durable data, use `with ctx.sqlite() as db:`. Set `db.row_factory = sqlite3.Row` before SELECT, and "
        "call fetchone/fetchall on the cursor returned by db.execute. Make slow work resumable with limit/batch_size, "
        "remaining_work, and next_cursor.\n"
        "Return concise status, summary, counts or ready outputs, and next_action. Read secrets from os.environ. "
        "Network clients use PEP 723 SOCKS dependencies plus ctx.requests_proxies()/ctx.proxy_url(). Write final files "
        "under /workspace/exports and reference them as $[/exports/...]; use GOBII_SCRATCH_DIR only for temporary data."
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
    discovery_triggers=(
        "recruitment sourcing",
        "candidate sourcing",
        "talent sourcing",
        "find candidates",
        "source candidates",
        "screen candidates",
        "shortlist candidates",
        "qualified candidate prospects",
        "talent scout",
    ),
    prompt_instructions=(
        "Recruitment sourcing means finding candidates worth recruiter review, not filling a quota with keyword "
        "matches. Treat the user's hiring criteria as the source of truth and preserve the difference between hard "
        "requirements, preferred signals, exclusions, and open questions.\n"
        "Do not begin active sourcing until you have enough role-specific information to screen responsibly. Strong "
        "intake fields include role title, client or hiring team, job description or responsibilities, required "
        "skills/credentials, seniority, work setup, location or time-zone rules, compensation or market constraints "
        "when provided, recruiter or delivery owner, test or screening assignment, target companies or backgrounds, "
        "and exclusion rules. If key screening criteria are missing, ask only for the missing facts. Do not treat "
        "phrases like 'start today', 'start sourcing', or 'start sourcing today' as approval for a title/location-only "
        "search when the user also says the job posting, requirements, required skills, or dealbreakers are not "
        "available. Those phrases only express urgency. Explicit partial-search approval must be unambiguous, such as "
        "the user saying they know the criteria are missing and still want a broad/general/partial search. Never tell "
        "the user that 'start today' counts as explicit approval despite missing screening criteria. If the user "
        "explicitly approves that partial search, proceed with the available facts and label the assumptions. If an "
        "intake question cannot be created because it is too broad or has too many options, ask one concise blocking "
        "question in chat and wait; the failed question attempt is not a user answer and is not approval to call "
        "sourcing tools.\n"
        "When materials conflict, prefer the most direct hiring-manager signal first, then intake notes/transcript, "
        "then intake summary, then job posting, then test assignment, then later user clarification. Use later user "
        "feedback to refine the search, but do not erase durable hard requirements unless the user clearly changes "
        "them.\n"
        "Use only job-relevant criteria. Do not evaluate, rank, infer, or filter candidates based on protected "
        "characteristics. For global or lower-cost-market searches, follow the user's country, compensation, work "
        "authorization, language, and remote/contract constraints without stereotyping individuals or implying "
        "quality from nationality.\n"
        "Choose sources based on the tools and permissions actually available. Prefer structured people/company "
        "sources such as LinkedIn data tools, Apollo, ATS/CRM exports, Google Sheets, or existing SQLite ledgers "
        "when they are connected and relevant. If a specific source is unavailable or blocked, use another approved "
        "source or explain the limitation; do not pretend the missing source was checked. Use `search_tools` only "
        "when you need to discover available tools or sources, not before every obvious sourcing action.\n"
        "Search in bounded, explainable batches. Convert requirements into concrete queries: role/title synonyms, "
        "must-have skills, target and adjacent companies, geography, remote/on-site rules, industry/domain signals, "
        "credentials, and exclusion terms. Expand from examples by archetype when the user says examples are not "
        "the complete list. Do not overfit to a single company list, title spelling, or source if adjacent profiles "
        "would satisfy the role.\n"
        "Treat explicit terms such as 'must', 'required', 'only', 'non-negotiable', and equivalent constraints as "
        "gates. Never relax them merely to reach a requested candidate count. Return fewer or zero qualified "
        "candidates when necessary. If partial matches are useful, put them in a clearly separate screening-leads "
        "or near-matches section, identify every failed or unknown gate, and never label them as qualified or as "
        "satisfying the request.\n"
        "Verify each recommended candidate against the hard requirements before delivery. At minimum, check current "
        "or recent title, company, location/work setup, role-relevant experience, source URL, and any explicit "
        "dealbreakers. Apply conservative judgment: exclude or mark low confidence when evidence is weak, stale, "
        "ambiguous, outside geography, too junior/senior, wrong function, wrong industry, or from an excluded company. "
        "Never present an unverified or weak match as a strong fit just to hit a requested count.\n"
        "Maintain a candidate ledger when the task spans more than one batch or has exclusion, feedback, or delivery "
        "history. Track profile URL or stable source id, name, current title/company, location, confidence, status, "
        "reasoning, source, delivery date, and recruiter feedback. Dedupe by profile URL first, then normalized name "
        "plus company/location. Before each delivery, check prior delivered/rejected candidates and current client "
        "or company exclusion lists when available.\n"
        "Respond to recruiter feedback as data. Mark rejected, standing, contacted, duplicate, excluded, or needs "
        "review candidates explicitly; identify the rule learned from the feedback; apply it to future sourcing; "
        "and, when useful, update the search strategy in one or two concrete changes.\n"
        "Deliver recruiter-ready output in the requested format. Candidate tables should include name, title, company, "
        "location, source/profile URL, confidence or tier, and concise fit notes tied to the role criteria. Separate "
        "tracks when the role has materially different searches, such as U.S. employee vs global contractor. Include "
        "counts, source coverage, exclusions applied, duplicates skipped, low-confidence caveats, and what remains "
        "to search. If the user asks for CSV only, deliver the CSV artifact without extra report files.\n"
        "For outreach, sequence enrollment, revealing paid contact data, or sending candidates to recruiters, summarize "
        "the exact side effects and recipients first unless the user has already clearly approved that action. Respect "
        "contact permissions and never invent recruiter recipients or candidate contact details.\n"
        "If source access is partial, a tool errors, or the requested count cannot be met responsibly, report the "
        "verified partial set, blocker, and next bounded search path. When fallback search and verification produce "
        "the requested batch, deliver immediately; do not repeat equivalent searches or add ledger work unless "
        "requested. Quality and criteria fidelity beat volume."
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
    tool_names=("discord_channel_subscriptions", "send_discord_message", "add_discord_reaction"),
    enables=(
        "receive Discord channel messages through the native Gobii Discord bot",
        "discover Discord guild channels claimed by the agent owner",
        "send Discord replies through Gobii bot webhooks using the agent name and avatar",
        "add emoji reactions to Discord messages in subscribed channels",
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
        "That link installs the Gobii bot only in the server selected during that authorization. "
        "Each additional server requires its own connect flow.\n"
        "After the user says Discord setup is complete, call `list_guilds` or `discover_channels` again. "
        "If the tool returns `selected_guild`, use that server and continue to channel discovery; do not ask the user to choose the server again.\n"
        "After guilds are connected, use `discover_channels` to list channels visible to the Gobii bot. If several channels are returned, ask the user to choose by channel name, "
        "then call `ensure` with the selected `guild_id`, `channel_id`, and `channel_name` so future channel messages wake this agent.\n"
        "Only ask the user for raw server or channel IDs if discovery fails or returns no useful choices. "
        "Do not request Discord server IDs or channel IDs as secrets.\n"
        "Use `send_discord_message` for outbound Discord replies to subscribed channels. Pass the channel ID when known; "
        "otherwise pass the exact channel name, adding the guild ID if the same name is subscribed in multiple servers. "
        "Pass `message` and the correct `will_continue_work` value. "
        "Write the message in Discord-compatible Markdown; raw HTML is rejected. "
        "Discord cannot render tables: never send pipe-separated columns with a hyphen-divider row, even as a summary. "
        "Use compact headings with bullets or bold labels. "
        f"To upload files: {SEND_TOOL_ATTACHMENTS_DESCRIPTION} "
        "The backend sends through a channel webhook using the agent's name and avatar.\n"
        "Use `add_discord_reaction` for lightweight social moments such as acknowledgement, thanks, agreement, humor, congratulations, or a shared win, even when no reaction was explicitly requested. "
        "For a lightweight Discord social moment, use one fitting reaction, then stop; do not also reply. A direct reply to someone else is not your social moment unless its text includes you or the room. Do not react to every message or stack reactions. Do not react to a serious question, request, blocker, or important nuance; give it a substantive reply instead. "
        "Pass the subscribed `channel_id`, the message's `discord_message_id` as `message_id`, one Unicode or Discord custom emoji, and the correct `will_continue_work` value. "
        "This tool only adds the Gobii bot's own reaction; it does not remove or manage other reactions.\n"
        "Use `list` before creating duplicates when the current subscription state is unclear. Use `disable` only when the user asks to stop receiving messages from a subscribed channel.\n"
        "If channel discovery says the Gobii bot cannot list channels, send the returned `connect_url` as the repair link. "
        "The repair flow is locked to that connected server."
    ),
)


WEBHOOKS_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=WEBHOOKS_SYSTEM_SKILL_KEY,
    name="Webhooks",
    search_summary=(
        "Create and manage native Gobii inbound webhook triggers and outbound webhook destinations, then send "
        "structured outbound webhook events."
    ),
    tool_names=("manage_inbound_webhooks", "manage_outbound_webhooks", "send_webhook_event"),
    enables=(
        "create callback URLs that let external provider events wake the agent",
        "inspect, update, rotate, and remove inbound webhook triggers",
        "configure outbound destinations and send structured JSON webhook events",
    ),
    use_when=(
        "the user wants an external service or provider event to trigger or wake the agent",
        "the user asks to create, inspect, update, rotate, or delete an inbound webhook",
        "the user asks to configure, manage, send, or trigger an outbound webhook",
        "the task needs a callback URL or HTTP endpoint for asynchronous provider events",
    ),
    query_aliases=(
        "webhook",
        "webhooks",
        "callback url",
        "http callback",
        "provider event trigger",
        "pipedream",
        "pipedream http trigger",
    ),
    discovery_triggers=(
        "webhook",
        "webhooks",
        "callback url",
        "events trigger you",
        "events wake you",
        "pipedream",
    ),
    prompt_instructions=(
        "Use native Gobii webhooks for webhook setup and delivery. An inbound webhook is a secret-bearing Gobii "
        "endpoint that an external service POSTs to so an event wakes this agent. An outbound webhook is an external "
        "destination configured in Gobii; `send_webhook_event` sends JSON to it. Keep those directions distinct.\n"
        "Prefer native Gobii webhooks over Pipedream. Use or recommend Pipedream only when the user explicitly asks "
        "for Pipedream, the integration itself is provided through Pipedream, or the provider requires a webhook "
        "protocol Gobii does not support. Explain an unsupported protocol before suggesting an alternative.\n"
        "For external events that should wake this agent, call `manage_inbound_webhooks` with action=list before "
        "creating anything, then create or reuse the intended trigger. Use action=get only when the exact "
        "secret-bearing endpoint is needed for provider registration. If the provider offers an API and the user "
        "authorized setup, register that returned URL through the provider API. Otherwise give the user the native "
        "URL and concise provider UI steps. Do not invent an endpoint, token, signature, or authentication header; "
        "the generated URL already contains Gobii's receiver secret.\n"
        "Use `manage_outbound_webhooks` to list or configure destinations. Use `send_webhook_event` only with a "
        "configured outbound webhook ID and a purpose-built JSON object. Do not create duplicate webhook entries.\n"
        "A clear user request for a specific create, update, rotate, or delete operation is sufficient authorization; "
        "do not ask for redundant confirmation. Never infer rotation or deletion merely while troubleshooting. Do not "
        "repeat secret-bearing URLs in ordinary status summaries or expose them to unrelated recipients."
    ),
    prompt_context_renderer=_webhooks_prompt_context,
)

META_GOBII_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=META_GOBII_SYSTEM_SKILL_KEY,
    name="Meta Gobii",
    search_summary=(
        "Coordinate persistent Gobiis as a control-plane skill, including team management inside the same owner scope."
    ),
    tool_names=META_GOBII_TOOL_NAMES,
    enables=(
        "list, inspect, create, update, and archive persistent Gobiis",
        "request Gobii creation through the existing human Create/Decline approval flow",
        "configure name, charter, schedule, active state, intelligence tier, daily credit limits, whitelist policy, and proactive opt-in",
        "create, list, update, and remove peer-agent links with message-window limits",
        "link Gobiis for peer briefings and read or wait on their timelines",
        "upload and list files in a Gobii filespace",
        "manage contacts, allowlists, pending contact requests, contact endpoints, and preferred owner-safe endpoints",
        "assign opaque secure values and configure child Gobii email accounts",
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
        "Meta Gobii is the broader control-plane skill for coordinating persistent Gobiis. Team management is one "
        "capability under Meta Gobii, not the skill identity.\n"
        "Use these tools only when the user is asking you to create, configure, link, brief, or maintain persistent "
        "Gobiis in this same owner or organization scope. Do not use them for ordinary research, writing, support, "
        "or content tasks that merely mention Gobii.\n"
        "Creating or making any Gobii is control-plane work even when the requested Gobii is temporary, one-off, "
        "prototype, exploratory, or for a single batch. Archiving Gobiis, changing daily credit/resource limits, "
        "and rewiring or briefing a Gobii graph are also control-plane work.\n"
        "Authorization boundary: every tool is scoped to the invoking Gobii's personal owner scope or organization. "
        "Never attempt to manage agents outside that accessible scope.\n"
        "Human approval boundary: before making any control-plane mutation, ask the human to approve a concise "
        "summary of the proposed change. Mutations include creating, updating, archiving, linking, unlinking, "
        "briefing or messaging Gobiis, uploading files, adding/removing/approving contacts, changing preferred "
        "contact endpoints, assigning secure values, configuring email, and changing schedules, resources, or "
        "intelligence tiers. After explicit approval, set "
        "user_confirmed=true only on Meta Gobii tools that expose it. For broad multi-Gobii operations, first summarize the "
        "scope and wait for higher-level confirmation.\n"
        "For initial team creation or team-management capability tests, do not create, link, brief, schedule, or "
        "message anything yet. First produce one concise non-duplicated proposal with exactly the requested team scope: role names, "
        "responsibilities, peer-link graph, and one initial briefing per Gobii, each shown once. Ask for "
        "approval once with a clear question at the end of the response. After approval, execute only that approved "
        "scope; do not add extra agents, domains, "
        "schedules, contacts, files, or invented scenarios unless the human asks for them.\n"
        "Schedule default: do not include schedules in new Gobii or team proposals unless the user explicitly asks "
        "for recurring, scheduled, ongoing, proactive, digest, watch, check-in, or cadence-based behavior. One-off, "
        "demo, setup-only, trial, prototype, exploratory, backfill, cleanup, research, candidate-screening, sales-list, "
        "project-team, reorganize, link/unlink, archive, resource, contact, file, and make-available requests stay "
        "unscheduled by default. If a schedule might help but the user did not request one, mention it only as an "
        "optional follow-up outside the approval scope or ask a clarifying question; never invent a cadence.\n"
        "Schedule approval scope: when creating, changing, or removing a schedule, include the exact schedule action "
        "and cadence/removal in the approval summary. Existing-agent schedule changes require explicit user intent "
        "and approval. If the user approved a scope that omitted schedules, keep schedules out of tool arguments.\n"
        "For team creation after approval, inspect config options and existing agents when useful, then create the "
        "requested Gobiis and link this invoking Gobii to each target it must brief. After each link is enabled, use "
        "send_agent_message for the initial briefing and for all later questions, handoffs, replies, status updates, "
        "and coordination; there is no `meta_gobii_send_agent_message` tool. If a needed manager-to-target link is not already part of the approved graph, include it "
        "in the proposal and obtain approval before creating it. There is no unlinked control-plane message fallback. "
        "A single-Gobii request that says to "
        "brief, hand off, or send updates stays one Gobii unless the user asks for a team or multiple Gobiis.\n"
        "Graph restructure/link/archive requests do not imply mutable setting updates; use meta_gobii_update_agent "
        "only when the user asks to change name, charter, schedule, resources, availability, policy, or tier.\n"
        "For specialist handoffs that should use the existing Create/Decline approval request flow, use "
        "meta_gobii_request_agent_creation. Do not call legacy spawn_agent directly; it is only a hidden compatibility "
        "path after Meta Gobii is enabled.\n"
        "Use contact tools only for contacts the human supplied, approved, or that are already known internal team contacts. "
        "Grant can_configure only to owner-approved contacts. Prefer manual allowlist semantics for explicit contacts.\n"
        "When summarizing contact changes, avoid echoing full email addresses or phone numbers unless the user needs "
        "the exact value; prefer names, channels, or masked contact values.\n"
        "Use file tools only with files the human provided or artifacts you created for these agents. When a Gobii must "
        "work from a provided/uploaded file, place that file in this invoking Gobii's filespace and attach it through "
        "send_agent_message so normal peer transfer semantics deliver it. Uploads accept small base64 files; do not "
        "fetch arbitrary remote URLs through these tools.\n"
        "When an API response contains credentials for another Gobii, discover and use the Secure credential "
        "delegation skill; never retrieve those values with ordinary HTTP, browser tools, files, chat, or SQLite.\n"
        "Known unsupported MCP-equivalent surfaces in this direct skill: arbitrary URL file fetch, ad hoc runtime sessions, "
        "and separate task/run abstractions."
    ),
)

SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL_KEY,
    name="Secure credential delegation",
    search_summary=(
        "Provision credentials from JSON APIs into other Gobiis without exposing returned secret values."
    ),
    tool_names=(SECURE_API_REQUEST_TOOL_NAME,),
    enables=(
        "extract selected API response fields into short-lived encrypted references",
        "assign one-use secure references to another Gobii through Meta Gobii",
        "configure child Gobii email accounts without copying passwords into chat or tool arguments",
    ),
    use_when=(
        "an API creates or returns credentials that must be assigned to another Gobii",
        "a manager Gobii needs to provision accounts for child Gobiis",
        "mailbox passwords, app passwords, tokens, or API keys must move between systems securely",
        "the user asks for automated credential distribution or secure secret handoff",
    ),
    query_aliases=(
        "secure credential delegation",
        "provision child credentials",
        "assign secrets to gobiis",
        "secure api response",
        "mailbox provisioning",
        "manager gobii credentials",
        "deploy email workers",
    ),
    discovery_triggers=(
        "secure credential delegation",
        "provision credentials",
        "assign secrets to gobiis",
        "child secret",
        "encrypted credentials",
        "service token returned",
        "mailbox provisioning",
        "email workers",
        "mailboxes",
        "app password",
        "app-password",
        "manager gobii credentials",
        "secure api response",
        "credential distribution",
        "secret handoff",
    ),
    prompt_instructions=(
        "Use `secure_api_request` when an API response may contain passwords, app passwords, tokens, OTPs, or other "
        "credentials. Never fetch that response with ordinary `http_request`, a browser, a custom tool, or SQLite. "
        "Map only safe scalar identifiers such as account ID, address, provider, and status under public_fields. "
        "Map every credential-bearing path under secret_fields. The returned `sv_...` references are opaque, "
        "short-lived, and one-use; never try to inspect, decode, persist, or send them to a human.\n"
        "For child provisioning, enable Meta Gobii too. Create or identify the child, then pass each secure reference "
        "directly to `meta_gobii_assign_agent_secret` or `meta_gobii_configure_agent_email`. A clear human request to "
        "provision the described accounts is confirmation for that exact scope; keep using user_confirmed=true only "
        "within it, and ask again before expanding the number of children or destinations.\n"
        "For generic API credentials, choose the narrowest real domain_pattern supported by the destination API and "
        "a stable secret key. Never install a mailbox credential as a generic agent secret; use "
        "`meta_gobii_configure_agent_email` so Gobii's existing send/receive infrastructure is configured. Custom "
        "SMTP/IMAP with an app password can be tested and activated automatically; OAuth mailboxes must be prepared "
        "with connection_mode=oauth2 and the returned setup URL given to the owner for the provider login. Do not "
        "substitute a mailbox login password when the provider requires OAuth or an app password.\n"
        "Process paginated provider responses in bounded pages. Preserve stable public account IDs so retries reuse "
        "the intended child. A secure reference may be retried only for the same destination; never reuse it for a "
        "different Gobii or secret slot. Report counts and setup status, not credentials or secure references."
    ),
)


COMPUTER_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=COMPUTER_SYSTEM_SKILL_KEY,
    name="Computer",
    search_summary="Use approved MCP tools on a connected macOS or Windows computer.",
    tool_names=(),
    enables=(
        "work with approved apps on a connected desktop",
        "use screen, keyboard, mouse, window, and local computer tools",
        "target one of several named computers",
    ),
    use_when=(
        "the user asks to perform a task on their computer",
        "the user asks to interact with their Mac or Windows desktop",
        "the user asks to use an application installed on their local machine",
    ),
    query_aliases=(
        "computer",
        "desktop",
        "local machine",
        "screen",
        "keyboard",
        "mouse",
        "windows computer",
        "mac computer",
        "computer.cpp",
    ),
    discovery_triggers=(
        "computer",
        "desktop",
        "local machine",
        "screen",
        "keyboard",
        "mouse",
        "windows",
        "mac",
    ),
    discoverable_without_tools=True,
    prompt_available=_computer_prompt_available,
    prompt_instructions=(
        "Use namespaced `mcp_computer_...` tools for work on a connected computer. Tool and server descriptions identify "
        "the device and app. If several computers are connected and the requester did not identify one, ask which named "
        "computer to use. Treat offline, paused, locked, permissions_required, and update_required as blocking states: "
        "report the state and do not claim success or blindly retry. If the connected computer state is none, direct the "
        f"requester to `{_app_integrations_url()}` to install and pair computer.cpp. Never suggest exposing a public IP, "
        "port forwarding, weakening firewall settings, or unrelated browser automation. Keep desktop mutations within the "
        "exact scope the requester approved."
    ),
    prompt_context_renderer=_computer_prompt_context,
    setup_instructions=f"Connect a computer at {_app_integrations_url()}.",
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
    WEBHOOKS_SYSTEM_SKILL.skill_key: WEBHOOKS_SYSTEM_SKILL,
    META_GOBII_SYSTEM_SKILL.skill_key: META_GOBII_SYSTEM_SKILL,
    SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL.skill_key: SECURE_CREDENTIAL_DELEGATION_SYSTEM_SKILL,
    COMPUTER_SYSTEM_SKILL.skill_key: COMPUTER_SYSTEM_SKILL,
}
