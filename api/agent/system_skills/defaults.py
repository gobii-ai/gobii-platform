"""Default code-defined system skill definitions."""

from api.agent.tools.meta_gobii_names import META_GOBII_SYSTEM_SKILL_KEY, META_GOBII_TOOL_NAMES

from .registry import SystemSkillDefinition, SystemSkillDocLink, SystemSkillField


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
        "Use `update_plan` to track steps and progress and render them to the user.\n"
        "Plans are for complex, ambiguous, or multi-phase work. Do not use plans as filler for simple or single-step queries.\n"
        "A good plan breaks work into meaningful, logically ordered, verifiable steps. Do not include steps you cannot actually do.\n"
        "Use public statuses exactly as `todo`, `doing`, and `done`. Use exactly one `doing` item while work remains.\n"
        "Every `update_plan` call overwrites all current steps, so include the complete current plan each time.\n"
        "The tool also accepts optional top-level `files` and `messages` deliverables. Deliverables are associated with the whole current plan, not individual steps.\n"
        "Use `files` for final user-visible artifacts created during the work, such as reports, CSV exports, PDFs, charts, or generated documents. Do not include scratch files or temporary downloads.\n"
        "Use `messages` only after sending a final user-facing report, answer, or summary through send_email, send_sms, or send_chat_message. Use the exact returned message_id UUID, or omit `messages`. If you are sending the final message in this turn, call the send tool first with will_continue_work=true, then call update_plan after the send tool returns. Do not include routine progress updates, peer agent messages from send_agent_message, placeholders, SQL snippets, URLs, or invented IDs.\n"
        "If finishing or updating a plan after producing final files or sending final user-facing messages, include those deliverables in the same `update_plan` call using `files` and/or `messages`.\n"
        "Because deliverables are replaced on every call, preserve any still-relevant previous `files` and `messages` when later updating the plan.\n"
        "After calling `update_plan`, do not repeat the whole plan in chat because the harness displays it.\n"
        "Before moving to the next command or phase, mark the previous step `done` after verifying the work actually succeeded.\n"
        "If the plan changes mid-task, call `update_plan` again with the full revised plan.\n"
        "Send the final user-facing report before marking the final step done. When finished, call `update_plan` with all steps marked `done`.\n\n"
        "Use a plan when:\n"
        "- The task is non-trivial and requires multiple actions.\n"
        "- There are logical phases or dependencies.\n"
        "- The work has ambiguity that benefits from outlining goals.\n"
        "- Intermediate checkpoints would help.\n"
        "- The user asked for multiple things.\n"
        "- The user explicitly asked for TODOs or the plan tool.\n"
        "- You discover extra steps you intend to finish before yielding."
    ),
    default_enabled=True,
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
    tool_names=("pipedream_trigger_subscriptions",),
    enables=(
        "receive Discord channel messages through Pipedream Connect triggers",
        "inspect active connected-app trigger subscriptions",
        "disable connected-app trigger subscriptions",
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
        "pipedream triggers",
        "slack receive",
        "slack messages",
    ),
    pipedream_app_slugs=("discord",),
    prompt_instructions=(
        "Use normal Pipedream app tools for outbound actions such as sending Discord messages.\n"
        "When calling Discord send-message tools, pass the selected Discord channel ID as `channel`, the text as `message`, "
        "and the correct `will_continue_work` value. The backend supplies default Discord presentation fields unless you explicitly override them.\n"
        "Use `pipedream_trigger_subscriptions` only to manage inbound app event subscriptions that wake this agent.\n"
        "V1 supports Discord `message.created` subscriptions for selected channel IDs. Do not create all-channel or mention-only subscriptions.\n"
        "Before asking the user for a Discord channel ID, call `pipedream_trigger_subscriptions` with `action=\"discover_targets\"` to list available channels. "
        "For Discord channel discovery, prefer this tool over Pipedream `retrieve_options` or `configure_component`.\n"
        "If several channels are returned, ask the user to choose by channel name, then call `ensure` with the selected target value as `channel_ids`. "
        "Include `channel_names` when you know human-readable names. "
        "Do not treat Discord channel setup as complete after discovery or outbound sending; after the channel is selected, call `ensure` so future channel messages wake this agent. "
        "Skip `ensure` only when the user clearly asks for a one-time outbound-only Discord post and does not expect replies, monitoring, or ongoing interaction.\n"
        "Only ask the user for a raw channel ID in normal conversation if target discovery fails or returns no useful choices. "
        "Do not request Discord server IDs or channel IDs as secrets. Server ID is not required for v1 setup.\n"
        "If the tool returns `action_required`, provide the connect URL to the user and stop until authorization completes.\n"
        "If `ensure` succeeds, then use the normal Discord send-message tool for any outbound message the user requested. "
        "If outbound sending succeeds but `ensure` has not succeeded, the agent will not receive Discord replies.\n"
        "Use `list` before creating duplicates when the current subscription state is unclear.\n"
        "Use `disable` only when the user asks to stop receiving messages from a subscribed app channel."
    ),
)

META_GOBII_TEAM_MANAGER_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key=META_GOBII_SYSTEM_SKILL_KEY,
    name="Meta Gobii Team Manager",
    search_summary=(
        "Create, configure, link, brief, and manage teams or graphs of persistent Gobiis inside the same owner scope."
    ),
    tool_names=META_GOBII_TOOL_NAMES,
    enables=(
        "list, inspect, create, update, and archive persistent Gobiis",
        "configure name, charter, schedule, active state, intelligence tier, daily credit limits, whitelist policy, and proactive opt-in",
        "create, list, update, and remove peer-agent links with message-window limits",
        "send briefings to Gobiis and read or wait on their timelines",
        "upload and list files in a Gobii filespace",
        "manage contacts, allowlists, pending contact requests, contact endpoints, and preferred owner-safe endpoints",
    ),
    use_when=(
        "the user asks to create a team of Gobiis",
        "the user asks to build or restructure an agent graph",
        "the user asks a Gobii to manage other Gobiis or act as a manager Gobii",
        "the user asks to link Gobiis together and brief them",
        "the user asks to manage persistent Gobii settings, schedules, contacts, allowlists, resource limits, or peer links",
        "the task is explicitly about coordinating multiple Gobiis or maintaining a Gobii team",
    ),
    query_aliases=(
        "meta gobii",
        "manager gobii",
        "team of gobiis",
        "gobii team",
        "agent team",
        "agent graph",
        "gobii graph",
        "create agents",
        "manage agents",
        "link agents",
        "brief agents",
        "restructure gobiis",
        "spawn gobiis",
    ),
    prompt_instructions=(
        "Use these tools only when the user is asking you to create, configure, link, brief, or maintain persistent "
        "Gobiis in this same owner or organization scope. Do not use them for ordinary research, writing, support, "
        "or content tasks that merely mention Gobii.\n"
        "Authorization boundary: every tool is scoped to the invoking Gobii's personal owner scope or organization. "
        "Never attempt to manage agents outside that accessible scope.\n"
        "For team creation, first inspect config options and existing agents when useful, then create the requested "
        "Gobiis, link them, and send each one a concise briefing with its role and handoff context.\n"
        "Ask for human confirmation before archiving agents, unlinking broad graph sections, removing contacts, "
        "raising intelligence tier, raising daily credit/resource limits, or making broad graph rewrites unless the "
        "human explicitly requested the exact change.\n"
        "Use contact tools only for contacts the human supplied, approved, or that are already known internal team contacts. "
        "Grant can_configure only to owner-approved contacts. Prefer manual allowlist semantics for explicit contacts.\n"
        "When summarizing contact changes, avoid echoing full email addresses or phone numbers unless the user needs "
        "the exact value; prefer names, channels, or masked contact values.\n"
        "Use file tools only with files the human provided or artifacts you created for these agents. Uploads accept small "
        "base64 files; do not fetch arbitrary remote URLs through these tools.\n"
        "Known unsupported MCP-equivalent surfaces in this direct skill: arbitrary URL file fetch, ad hoc runtime sessions, "
        "and separate task/run abstractions."
    ),
)


DEFAULT_SYSTEM_SKILL_DEFINITIONS = {
    RUNTIME_PLANNING_SYSTEM_SKILL.skill_key: RUNTIME_PLANNING_SYSTEM_SKILL,
    META_ADS_SYSTEM_SKILL.skill_key: META_ADS_SYSTEM_SKILL,
    CONNECTED_APP_CHANNELS_SYSTEM_SKILL.skill_key: CONNECTED_APP_CHANNELS_SYSTEM_SKILL,
    META_GOBII_TEAM_MANAGER_SYSTEM_SKILL.skill_key: META_GOBII_TEAM_MANAGER_SYSTEM_SKILL,
}
