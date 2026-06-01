"""Default code-defined system skill definitions."""

from api.agent.tools.custom_tool_names import CREATE_CUSTOM_TOOL_NAME, CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL_KEY
from api.agent.tools.attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
from api.agent.tools.meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY, META_GOBII_TOOL_NAMES

from .registry import SystemSkillDefinition, SystemSkillDocLink, SystemSkillField


GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY = "google_sheets_native"


RUNTIME_PLANNING_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key="runtime_planning",
    name="Runtime Planning",
    search_summary="Track multi-step runtime work with visible plan steps and deliverables.",
    tool_names=("update_plan",),
    enables=(
        "visible task plans",
        "runtime step tracking",
        "file and message deliverable references",
    ),
    use_when=(
        "work is non-trivial and requires multiple actions",
        "work has logical phases or dependencies",
        "work has ambiguity that benefits from outlining goals",
        "intermediate checkpoints would help",
        "the user asked for multiple things",
        "the user explicitly asked for TODOs or a plan",
        "extra steps are discovered before yielding",
    ),
    query_aliases=("plan", "planning", "todo", "todos", "update plan", "task steps"),
    prompt_instructions=(
        "Use `update_plan` only for substantial multi-step work where a visible plan helps. "
        "Keep plans short, current, and verifiable; each call replaces the full active plan. "
        "Send the final user-facing report before any final completion update."
    ),
    default_enabled=True,
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
        "Use `create_custom_tool` to create or update sandboxed Python tools when the work is repetitive, "
        "deterministic, structured-data oriented, or would otherwise require several similar tool calls. "
        "A short tool beats manually shuttling rows, JSON, or API responses through context.\n"
        "Strong triggers: repeated MCP/API calls, pagination/cursors, scraping fan-out, sync/import jobs, "
        "bulk INSERT/UPDATE/UPSERT work, row-by-row transforms, validation/dedupe, retries/backoff, "
        "checkpoint/resume flows, exports, and reports derived from shared SQLite data.\n"
        "Before the first `create_custom_tool` call, check the draft for common rejections: exact import "
        "`from _gobii_ctx import main`; exact final line `if __name__ == '__main__': main(run)`; "
        "imports cover referenced modules, e.g. `import sqlite3` before `sqlite3.Row`; "
        "`parameters_schema.required` requires real source inputs plus "
        "destinations/filters/limits/dates; SQLite: `with ctx.sqlite() as db:`, never `db = ctx.sqlite()`; "
        "batch/limit tools return `remaining_work`/`next_cursor`; side-effect "
        "tools return `do_not_repeat_manually=true` plus next_action text exactly like 'Do not repeat manually; verify read-only; "
        "do not append/add/update again.'\n"
        "Development loop: call `create_custom_tool(source_path='/tools/my_tool.py', source_code=...)` first. "
        "If rejected, fix every listed issue and retry create_custom_tool, not create_file. Do not pass only `source_path` unless "
        "that file already exists. Invoke `custom_*`, inspect result/error, patch the same file with "
        "`file_str_replace`, then re-run. Start with a small sample or limit, verify, then widen scope.\n"
        "Source format: scripts run via `uv run`; add PEP 723 third-party deps, never stdlib deps; "
        "define `def run(params, ctx): ...`.\n"
        "Expose useful runtime parameters instead of hardcoding sample data, ids, filters, table names, URLs, "
        "limits, cursors, or destinations. Never invoke a custom tool with empty params just because defaults exist; "
        "pass concrete runtime values unless the tool intentionally reads verified config/state and returns resolved targets.\n"
        "For slow network, API, MCP, Google Sheets, backfill, and sync tools, make the tool chunkable by default: "
        "accept `limit` or `batch_size` plus status/id/date filters, persist progress in SQLite, and re-run bounded batches. Avoid all-or-nothing full-table batches that can time out; "
        "if a batch times out, patch it for smaller resumable batches instead of falling back to manual single-action loops.\n"
        "Write durable data directly to the shared SQLite DB. Keep queries inside the `with ctx.sqlite() as db:` "
        "block because after the block exits the DB is closed. Use cursor.rowcount/`SELECT changes()`; "
        "set `db.row_factory = sqlite3.Row` before SELECT/fetchall, because later changes do not convert tuples and "
        "rows are not `row.get(...)`. Treat `ctx.sqlite_db_path` as advanced. Do not ATTACH sandbox file paths in `sqlite_batch`.\n"
        "Use `ctx.call_tool(name, params)` to call enabled agent tools, MCP tools, builtins, or other `custom_*` "
        "tools from inside Python. For tool-to-tool calls, do not manage proxy or bridge transport yourself; "
        "`ctx.call_tool()` handles the internal bridge.\n"
        "Path rules: `/tools/my_tool.py` and `/exports/report.txt` are filespace paths for Gobii tool arguments. "
        "Inside custom-tool Python, write real files under `/workspace/...`, for example "
        "`Path('/workspace/exports/report.txt')`; do not use `open('/exports/report.txt', ...)` in custom-tool code. "
        "After writing `/workspace/exports/report.txt`, return or reference the user-facing path `$[/exports/report.txt]`.\n"
        "Secrets are available as env vars via `os.environ`. Never hardcode credentials. If a needed env var is "
        "missing, request it with `secure_credentials_request` using `secret_type='env_var'`, not a domain-scoped "
        "credential. Use the exact env var names shown in the secrets/env_var configuration.\n"
        "Network code needs SOCKS5 proxy support: use `requests[socks]` or `httpx[socks]`, declare deps in PEP 723, "
        "read proxy env vars, and prefer `ctx.requests_proxies()` or `ctx.proxy_url()`; do not rely on direct HTTPS tunneling.\n"
        "Return values must be helpful to the downstream agent, especially after writes or syncs. Every success or "
        "error return dict should include `next_action`. Keep returns concise: status, summary, what changed or "
        "which outputs are ready, counts, side effects, target resource ids/names, source filters/date ranges, "
        "skipped/duplicate counts, remaining work or cursor, and verification guidance. Name "
        "ready outputs specifically, such as `direct_post_urls`, `scrape_ready_urls`, `rows_written`, or "
        "`records_to_sync`. Validator/classifier tools should return accepted ready-to-use values, rejected inputs "
        "with reasons, the rule used, and whether more inputs are needed. For completed writes, include "
        "`do_not_repeat_manually=true` and source-code next_action text exactly like "
        "'Do not repeat manually; verify read-only; do not append/add/update again.'\n"
        "Useful pattern: fetch/call tools -> normalize -> write SQLite tables -> return summary, counts, outputs, "
        "remaining work, and verification. Once stable, save the workflow as a skill referencing the canonical `custom_*` tool id."
    ),
)


GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL_KEY,
    name="Google Sheets",
    search_summary="Read and update selected Google Sheets through the native Google Drive integration.",
    tool_names=("http_request",),
    enables=(
        "read Google Sheets metadata and worksheet names",
        "read spreadsheet ranges and rows",
        "append rows to selected spreadsheets",
        "update ranges in selected spreadsheets",
        "use native Google Drive OAuth with drive.file access",
    ),
    use_when=(
        "the user asks to read a Google Sheet",
        "the user asks to update, append, or write spreadsheet rows",
        "the user asks to inspect worksheets, tabs, ranges, cells, or formulas in Google Sheets",
        "the work references a spreadsheet selected through the native Google Drive integration",
    ),
    query_aliases=(
        "google sheets",
        "sheets",
        "spreadsheet",
        "worksheet",
        "google sheet",
        "sheets api",
        "drive file spreadsheet",
    ),
    prompt_instructions=(
        "Use `http_request` for Google Sheets API calls. Native Google Drive OAuth is applied automatically for "
        "`https://sheets.googleapis.com/` requests when the owner has connected Google Drive.\n"
        "Only assume access to spreadsheets listed in the Accessible Google Sheets section below. The native "
        "integration uses Google `drive.file`, so unlisted spreadsheets may fail unless the app created them or the "
        "user selected them in the Google Drive integration.\n"
        "Common calls:\n"
        "- Spreadsheet metadata and sheet tabs: GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}\n"
        "- Read values: GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{url_encoded_range}\n"
        "- Update values: PUT https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{url_encoded_range}?valueInputOption=USER_ENTERED with JSON body {\"values\": [[...]]}\n"
        "- Append rows: POST https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{url_encoded_range}:append?valueInputOption=USER_ENTERED with JSON body {\"values\": [[...]]}\n"
        "If the requested spreadsheet is not listed, ask the user to choose it through the Google Drive native "
        "integration before making Sheets API calls for that file."
    ),
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
    setup_instructions=(
        "Register as a Meta developer, create a Business app with the Marketing API product, create a system user, "
        "assign the app and ad account, generate a system user token with ads_read access, and then fill in the "
        "profile fields below."
    ),
    setup_steps=(
        "Register the real Facebook admin account as a Meta developer before trying to create the app.",
        "Create a Business app and make sure the Marketing API product is actually added to that app.",
        "Capture the App ID and App Secret from App Settings -> Basic.",
        "Create a system user in Business Settings and assign the app plus the ad account to it.",
        "Generate a system user token with ads_read access. If Meta sends it for approval, another business admin must approve it in Business Settings -> Requests.",
        "Fill in the profile with the App ID, App Secret, system user token, and default ad account ID.",
        "Optional but recommended for serious performance monitoring: add the Pixel or dataset ID so the agent can monitor conversion quality and event health.",
    ),
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
    troubleshooting_tips=(
        "If developers.facebook.com/apps keeps bouncing to a marketing or public landing page, complete developer registration first.",
        "Do not use the Meta app flow that says 'Create & manage app ads with Meta Ads Manager' because it does not include Marketing API.",
        "If token generation says approval was requested, the setup is not broken. Another business admin must approve it in Business Settings -> Requests.",
        "If the token works but no ad accounts are returned, double-check that the system user was assigned both the app and the ad account.",
        "If conversion-quality monitoring fails, make sure the system user or token also has access to the Pixel or dataset in Business Manager.",
    ),
    bootstrap_profile_key="default",
    bootstrap_profile_label="Primary Meta Ads Profile",
)


CONNECTED_APP_CHANNELS_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key="connected_app_channels",
    name="Connected App Channels",
    search_summary="Provision inbound message subscriptions for connected apps like Discord.",
    tool_names=("discord_channel_subscriptions", "discord_send_message"),
    enables=(
        "receive Discord channel messages through the native Gobii Discord bot",
        "discover Discord guild channels claimed by the agent owner",
        "send Discord replies through Gobii bot webhooks using the agent name and avatar",
        "inspect and disable connected-app channel subscriptions",
        "turn selected app channels into agent conversations",
    ),
    use_when=(
        "the user wants the agent to receive Discord messages",
        "the user asks to monitor or listen to a Discord channel",
        "the user wants the agent to interact with a Discord server or channel over time",
        "the user wants messages from a connected app to wake the agent",
        "the user asks whether connected app channel subscriptions are active",
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
        "Use `discord_send_message` for outbound Discord replies to subscribed channels. Pass `channel_id`, `message`, and the correct `will_continue_work` value. "
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
        "the user asks to deploy Gobiis or request a specialist Gobii",
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
        "contact endpoints, and changing schedules, resources, or intelligence tiers. Pass user_confirmed=true "
        "only after that explicit approval. For broad operations involving multiple Gobiis, first summarize the "
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
        "requested Gobiis, link them, and brief each one. A single-Gobii request that says to brief, hand off, or "
        "send updates stays one Gobii unless the user asks for a team or multiple Gobiis.\n"
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
        "work from a provided/uploaded file, copy that file into its filespace before briefing it and attach the path in "
        "meta_gobii_send_agent_message. Uploads accept small base64 files; do not fetch arbitrary remote URLs through these tools.\n"
        "Known unsupported MCP-equivalent surfaces in this direct skill: arbitrary URL file fetch, ad hoc runtime sessions, "
        "and separate task/run abstractions."
    ),
)


DEFAULT_SYSTEM_SKILL_DEFINITIONS = {
    RUNTIME_PLANNING_SYSTEM_SKILL.skill_key: RUNTIME_PLANNING_SYSTEM_SKILL,
    CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL.skill_key: CUSTOM_TOOL_DEVELOPMENT_SYSTEM_SKILL,
    GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL.skill_key: GOOGLE_SHEETS_NATIVE_SYSTEM_SKILL,
    META_ADS_SYSTEM_SKILL.skill_key: META_ADS_SYSTEM_SKILL,
    CONNECTED_APP_CHANNELS_SYSTEM_SKILL.skill_key: CONNECTED_APP_CHANNELS_SYSTEM_SKILL,
    META_GOBII_SYSTEM_SKILL.skill_key: META_GOBII_SYSTEM_SKILL,
}
