# Codebase Line Allocation Report

Generated for `/Users/mattworkpro/git/gobii/gobii_platform` from tracked files.

## Methodology

- Counted nonblank lines, not comment-stripped logical statements. Physical line counts are available in `codebase_line_allocation_files.csv`.
- Included source, Django templates, config, scripts, migrations, and vendor/reference source that are tracked in git.
- Excluded tests, evals, docs/text content, dependency/build output, media/staticfiles output, lockfiles, and binary assets.
- Included inventory: 1,644 files, 317,865 nonblank LOC.
- First-party product/runtime LOC excluding domain model/schema history and vendor/reference UI: 274,337.

Excluded file counts by reason:

| Reason | Files |
|---|---:|
| tests/docs | 448 |
| assets/locks/binary | 184 |
| evals | 76 |
| docs/text | 70 |
| non-source | 69 |
| tests | 26 |
| locks | 2 |

## Allocation By Feature

| Feature | LOC | % | Files |
|---|---:|---:|---:|
| Frontend console app | 83,478 | 26.3% | 354 |
| Backend platform and data model | 70,561 | 22.2% | 663 |
| Agent runtime backend | 65,054 | 20.5% | 144 |
| Server-rendered console | 29,876 | 9.4% | 91 |
| Public site, marketing, and templates | 27,117 | 8.5% | 155 |
| Vendor and reference UI | 13,758 | 4.3% | 71 |
| Sandbox, infrastructure, and operations | 10,263 | 3.2% | 65 |
| Utilities and examples | 7,942 | 2.5% | 52 |
| Billing and subscription backend | 4,007 | 1.3% | 15 |
| Static source assets | 3,182 | 1.0% | 15 |
| Analytics, attribution, and marketing events | 2,627 | 0.8% | 19 |

## Allocation By Top-Level Path

| Path | LOC | % | Files |
|---|---:|---:|---:|
| `api` | 135,615 | 42.7% | 807 |
| `frontend` | 83,133 | 26.2% | 349 |
| `console` | 29,876 | 9.4% | 91 |
| `pages` | 15,458 | 4.9% | 58 |
| `vendor` | 13,758 | 4.3% | 71 |
| `templates` | 8,994 | 2.8% | 78 |
| `util` | 4,718 | 1.5% | 21 |
| `config` | 4,220 | 1.3% | 19 |
| `static` | 3,527 | 1.1% | 20 |
| `proprietary` | 2,665 | 0.8% | 19 |
| `sandbox_server` | 2,113 | 0.7% | 17 |
| `billing` | 1,773 | 0.6% | 10 |
| `setup` | 1,528 | 0.5% | 8 |
| `scripts` | 1,491 | 0.5% | 8 |
| `marketing_events` | 1,377 | 0.4% | 15 |
| `misc` | 1,262 | 0.4% | 7 |
| `tasks` | 1,129 | 0.4% | 2 |
| `.github` | 1,088 | 0.3% | 6 |
| `agents` | 896 | 0.3% | 2 |
| `middleware` | 722 | 0.2% | 5 |
| `docker` | 341 | 0.1% | 8 |
| `compose.yaml` | 339 | 0.1% | 1 |
| `observability.py` | 333 | 0.1% | 1 |
| `docker-compose.dev.yaml` | 323 | 0.1% | 1 |
| `constants` | 296 | 0.1% | 8 |
| `agent_namer.py` | 258 | 0.1% | 1 |
| `celery_task_counter.py` | 247 | 0.1% | 1 |
| `pyproject.toml` | 152 | 0.0% | 1 |
| `turnstile_signup.py` | 109 | 0.0% | 1 |
| `templatetags` | 88 | 0.0% | 5 |
| `manage.py` | 17 | 0.0% | 1 |
| `run-with-secrets.sh` | 14 | 0.0% | 1 |
| `.factory` | 5 | 0.0% | 1 |

## Feature And Subfeature Detail

### Frontend console app (83,478 LOC, 354 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Agent chat workspace components | 21,099 | 6.6% | 105 | `frontend/src/components/agentChat/AgentComposer.tsx` (2,096), `frontend/src/components/agentChat/AgentChatLayout.tsx` (1,794), `frontend/src/components/agentChat/ToolClusterLivePreview.tsx` (1,505), `frontend/src/components/agentChat/insights/AgentSetupInsight.tsx` (629) |
| Route-level screens | 19,039 | 6.0% | 45 | `frontend/src/screens/AgentChatPage.tsx` (3,951), `frontend/src/screens/AgentDetailScreen.tsx` (3,468), `frontend/src/screens/AgentEmailSettingsScreen.tsx` (1,163), `frontend/src/screens/AgentAuditScreen.tsx` (988) |
| Frontend styling | 9,747 | 3.1% | 12 | `frontend/src/styles/agentChatLegacy.css` (7,724), `frontend/src/styles/insights.css` (976), `frontend/src/styles/consoleShell.css` (267), `frontend/src/styles/simplifiedChat.css` (245) |
| LLM configuration UI | 4,809 | 1.5% | 12 | `frontend/src/components/llmConfig/useRoutingTierActions.tsx` (1,050), `frontend/src/components/llmConfig/shared.tsx` (918), `frontend/src/components/llmConfig/ProviderCard.tsx` (609), `frontend/src/components/llmConfig/LlmConfigView.tsx` (592) |
| MCP and integrations UI components | 4,603 | 1.4% | 11 | `frontend/src/components/mcp/McpServerFormModal.tsx` (984), `frontend/src/components/mcp/PipedreamAppsModal.tsx` (935), `frontend/src/components/mcp/AgentPipedreamAppsModal.tsx` (568), `frontend/src/components/mcp/DiscordNativeAppModal.tsx` (485) |
| Frontend API clients | 4,088 | 1.3% | 31 | `frontend/src/api/agentChat.ts` (731), `frontend/src/api/llmConfig.ts` (561), `frontend/src/api/mcp.ts` (532), `frontend/src/api/http.ts` (233) |
| Agent, system, and secret settings components | 3,727 | 1.2% | 30 | `frontend/src/components/agentFiles/FileTable.tsx` (357), `frontend/src/components/systemSkills/SystemSkillProfileFormModal.tsx` (272), `frontend/src/components/systemStatus/StatusSections.tsx` (267), `frontend/src/components/agentAudit/EventRows.tsx` (259) |
| Realtime and page hooks | 3,684 | 1.2% | 25 | `frontend/src/hooks/useTimelineCacheInjector.ts` (508), `frontend/src/hooks/useAgentChatSocket.ts` (494), `frontend/src/hooks/useAgentWebSession.ts` (454), `frontend/src/hooks/useSimplifiedTimeline.ts` (300) |
| Tool metadata and live tool UI | 2,884 | 0.9% | 5 | `frontend/src/components/tooling/toolMetadata.ts` (1,574), `frontend/src/components/tooling/agentConfigSql.ts` (483), `frontend/src/components/tooling/brightdata.ts` (387), `frontend/src/components/tooling/sqliteDisplay.ts` (373) |
| Frontend types, utilities, and constants | 2,514 | 0.8% | 29 | `frontend/src/util/schedule.ts` (440), `frontend/src/types/agentChat.ts` (284), `frontend/src/types/agentSettings.ts` (213), `frontend/src/util/sanitize.ts` (186) |
| Shared component primitives | 2,135 | 0.7% | 17 | `frontend/src/components/common/SubscriptionUpgradePlans.tsx` (350), `frontend/src/components/common/MobileSheet.tsx` (206), `frontend/src/components/common/InsightGauge.tsx` (134), `frontend/src/components/common/SettingsSurface.tsx` (132) |
| Client state stores | 1,946 | 0.6% | 4 | `frontend/src/stores/agentChatStore.ts` (906), `frontend/src/stores/subscriptionStore.ts` (449), `frontend/src/stores/agentChatTimeline.ts` (379), `frontend/src/stores/agentAuditStore.ts` (212) |
| Usage analytics UI components | 1,584 | 0.5% | 11 | `frontend/src/components/usage/UsageAgentLeaderboard.tsx` (385), `frontend/src/components/usage/UsageTrendSection.tsx` (307), `frontend/src/components/usage/UsageMetricsGrid.tsx` (234), `frontend/src/components/usage/UsageRangeControls.tsx` (150) |
| Other frontend components | 860 | 0.3% | 3 | `frontend/src/components/homepage/HomepageIntegrationsModal.tsx` (627), `frontend/src/components/settings/CustomInstructionsSection.tsx` (128), `frontend/src/components/PingStatusCard.tsx` (105) |
| Frontend build and entry configuration | 759 | 0.2% | 14 | `frontend/src/main.tsx` (214), `frontend/src/prequal.ts` (159), `frontend/src/homepageIntegrations.tsx` (65), `frontend/package.json` (61) |

### Backend platform and data model (70,561 LOC, 663 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Django domain models and schema history | 29,770 | 9.4% | 492 | `api/models.py` (12,042), `api/migrations/0257_intelligencetier_alter_browserllmtier_options_and_more.py` (559), `api/migrations/0249_plan_versioning.py` (440), `api/migrations/0250_seed_plan_versions.py` (432) |
| Django admin surfaces | 7,032 | 2.2% | 25 | `api/admin.py` (5,512), `api/admin_forms.py` (869), `api/templates/admin/smsnumber_release_candidates.html` (94), `api/templates/admin/persistentagent_bulk_proactive_outreach.html` (69) |
| Integrations and MCP services | 6,887 | 2.2% | 13 | `api/services/remote_mcp.py` (1,995), `api/services/native_integrations.py` (1,095), `api/services/discord_bot.py` (937), `api/services/pipedream_trigger_subscriptions.py` (866) |
| API views, serializers, webhooks, auth, and routing | 5,715 | 1.8% | 38 | `api/views.py` (1,012), `api/webhooks.py` (879), `api/serializers.py` (642), `api/public_profiles.py` (405) |
| Agent lifecycle and configuration services | 4,401 | 1.4% | 17 | `api/services/agent_debug_trace.py` (697), `api/services/persistent_agent_restore.py` (521), `api/services/template_clone.py` (432), `api/services/owner_execution_pause.py` (425) |
| Celery and periodic task implementations | 4,279 | 1.3% | 17 | `api/tasks/browser_agent_tasks.py` (2,045), `api/tasks/proxy_tasks.py` (546), `api/tasks/billing_rollup.py` (489), `api/tasks/soft_expiration_task.py` (245) |
| Sandbox and compute services | 4,089 | 1.3% | 6 | `api/services/sandbox_compute.py` (2,011), `api/services/sandbox_kubernetes.py` (1,635), `api/services/sandbox_filespace_sync.py` (194), `api/services/sandbox_compute_lifecycle.py` (132) |
| Billing, credits, trials, and growth services | 3,263 | 1.0% | 13 | `api/services/referral_service.py` (713), `api/services/trial_abuse.py` (522), `api/services/user_fingerprint.py` (520), `api/services/trial_promos.py` (281) |
| System, tool, prompt, and skill settings | 1,533 | 0.5% | 8 | `api/services/system_settings.py` (431), `api/services/tool_settings.py` (211), `api/services/system_skill_profiles.py` (204), `api/services/global_skill_json.py` (200) |
| Management commands and maintenance jobs | 1,480 | 0.5% | 20 | `api/management/commands/run_imap_idlers.py` (392), `api/management/commands/migrate_pending_agent_processing.py` (188), `api/management/commands/buy_twilio_numbers.py` (148), `api/management/commands/reset_secrets_database.py` (120) |
| User, organization, secrets, and contact services | 1,123 | 0.4% | 7 | `api/services/web_sessions.py` (489), `api/services/sms_number_inventory.py` (256), `api/services/user_flags.py` (130), `api/services/email_verification.py` (108) |
| General backend service layer | 989 | 0.3% | 7 | `api/services/burn_rate_snapshots.py` (285), `api/services/cron_throttle.py` (261), `api/services/web_chat_followups.py` (157), `api/services/dedicated_proxy_service.py` (124) |

### Agent runtime backend (65,054 LOC, 144 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Run loop, prompts, LLM config, result handling | 21,387 | 6.7% | 33 | `api/agent/core/event_processing.py` (5,864), `api/agent/core/prompt_context.py` (4,537), `api/agent/core/result_analysis.py` (2,335), `api/agent/core/llm_config.py` (1,398) |
| Agent data analysis and summarization tools | 9,592 | 3.0% | 15 | `api/agent/tools/sqlite_batch.py` (1,627), `api/agent/tools/sqlite_analysis.py` (1,210), `api/agent/tools/sqlite_guardrails.py` (1,051), `api/agent/tools/json_goldilocks.py` (996) |
| Tool registry, MCP, native/custom tools | 8,544 | 2.7% | 11 | `api/agent/tools/mcp_manager.py` (2,846), `api/agent/tools/search_tools.py` (1,395), `api/agent/tools/tool_manager.py` (1,375), `api/agent/tools/custom_tools.py` (1,144) |
| General agent tool implementations | 7,585 | 2.4% | 23 | `api/agent/tools/meta_gobii.py` (2,034), `api/agent/tools/meta_ads.py` (1,825), `api/agent/tools/context_hints.py` (808), `api/agent/tools/plan.py` (574) |
| Email, SMS, web chat, and human-input communications | 6,006 | 1.9% | 18 | `api/agent/comms/human_input_requests.py` (1,804), `api/agent/comms/outbound_delivery.py` (1,247), `api/agent/comms/message_service.py` (858), `api/agent/comms/adapters.py` (456) |
| Content and file generation tools | 2,803 | 0.9% | 8 | `api/agent/tools/create_video.py` (648), `api/agent/tools/create_pdf.py` (549), `api/agent/tools/create_chart.py` (528), `api/agent/tools/create_image.py` (514) |
| Communication and human-facing tools | 2,761 | 0.9% | 11 | `api/agent/tools/web_chat_sender.py` (422), `api/agent/tools/email_sender.py` (354), `api/agent/tools/outbound_duplicate_guard.py` (298), `api/agent/tools/webhook_sender.py` (275) |
| Agent task orchestration and peer communication | 2,489 | 0.8% | 10 | `api/agent/tasks/process_events.py` (631), `api/agent/tasks/email_polling.py` (487), `api/agent/peer_comm.py` (434), `api/agent/tasks/agent_avatar.py` (409) |
| System skill definitions and defaults | 1,551 | 0.5% | 5 | `api/agent/system_skills/defaults.py` (817), `api/agent/system_skills/native_api_cookbooks.py` (357), `api/agent/system_skills/service.py` (231), `api/agent/system_skills/registry.py` (131) |
| Browser action execution | 1,311 | 0.4% | 6 | `api/agent/browser_actions/captcha_solver.py` (621), `api/agent/browser_actions/file_upload.py` (260), `api/agent/browser_actions/artifacts.py` (180), `api/agent/browser_actions/web_search.py` (118) |
| Agent files and workspace handling | 1,025 | 0.3% | 4 | `api/agent/files/filespace_service.py` (468), `api/agent/files/attachment_helpers.py` (435), `api/agent/files/filesystem_prompt.py` (121), `api/agent/files/__init__.py` (1) |

### Server-rendered console (29,876 LOC, 91 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| General console views and forms | 14,021 | 4.4% | 24 | `console/api_views.py` (7,417), `console/views.py` (3,224), `console/system_status.py` (668), `console/forms.py` (667) |
| Agent chat realtime/timeline server support | 3,383 | 1.1% | 9 | `console/agent_chat/timeline.py` (1,409), `console/agent_chat/signals.py` (611), `console/agent_chat/suggestions.py` (354), `console/agent_chat/consumers.py` (311) |
| Billing, usage, and credit console endpoints | 2,469 | 0.8% | 7 | `console/usage_views.py` (828), `console/billing_update_service.py` (766), `console/billing_initial_data.py` (279), `console/llm_tier_usage.py` (221) |
| Agent settings services and mutations | 2,246 | 0.7% | 4 | `console/agent_settings/service.py` (2,242), `console/agent_settings/__init__.py` (2), `console/agent_settings/mutations.py` (1), `console/agent_settings/payload.py` (1) |
| Console templates | 2,114 | 0.7% | 23 | `console/templates/console/staff_agent_audit_export_viewer.js` (400), `console/templates/console/staff_agent_audit_export.html` (309), `console/templates/index.html` (266), `console/templates/console/includes/_context_switcher.html` (182) |
| Integrations, secrets, API keys, and system skills | 1,678 | 0.5% | 6 | `console/native_integrations_api.py` (395), `console/secrets_api_views.py` (342), `console/discord_api.py` (260), `console/api_keys_api_views.py` (257) |
| Agent management console endpoints | 1,400 | 0.4% | 6 | `console/agent_creation.py` (594), `console/agent_addons.py` (438), `console/agent_cards.py` (170), `console/agent_reassignment.py` (93) |
| Agent audit views and realtime export | 1,009 | 0.3% | 7 | `console/agent_audit/export.py` (312), `console/agent_audit/events.py` (290), `console/agent_audit/serializers.py` (185), `console/agent_audit/timeline.py` (133) |
| Email settings | 824 | 0.3% | 3 | `console/email_settings/views.py` (798), `console/email_settings/constants.py` (26), `console/email_settings/__init__.py` (0) |
| Organization console endpoints | 732 | 0.2% | 2 | `console/organization_api_views.py` (715), `console/role_constants.py` (17) |

### Public site, marketing, and templates (27,117 LOC, 155 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Public pages Django app | 10,332 | 3.3% | 32 | `pages/views.py` (3,908), `pages/signals.py` (3,830), `pages/library_views.py` (359), `pages/utils_markdown.py` (282) |
| Public pages templates | 5,126 | 1.6% | 26 | `pages/templates/home.html` (1,655), `pages/templates/comparisons/detail_lindy.html` (409), `pages/templates/comparisons/detail_zapier_agents.html` (400), `pages/templates/comparisons/detail_n8n.html` (388) |
| Solution landing page templates | 4,402 | 1.4% | 9 | `templates/solutions/sales.html` (765), `templates/solutions/engineering.html` (754), `templates/solutions/recruiting.html` (751), `templates/solutions/health-care.html` (676) |
| Shared Django templates | 2,991 | 0.9% | 25 | `templates/base.html` (577), `templates/includes/_unified_header_nav.html` (440), `templates/page.html` (314), `templates/partials/_immersive_overlay_script.html` (232) |
| Proprietary marketing templates and emails | 1,410 | 0.4% | 12 | `proprietary/templates/pricing.html` (337), `proprietary/templates/home/_value_sections.html` (232), `proprietary/templates/prequalify.html` (169), `proprietary/templates/support.html` (125) |
| Proprietary marketing app | 1,255 | 0.4% | 7 | `proprietary/views.py` (953), `proprietary/utils_blog.py` (157), `proprietary/forms.py` (64), `proprietary/defaults.py` (39) |
| Auth/account templates | 1,049 | 0.3% | 22 | `templates/account/_login_content.html` (174), `templates/account/_signup_content.html` (144), `templates/account/_identity_signal_helpers.html` (126), `templates/account/email.html` (116) |
| Transactional email templates | 347 | 0.1% | 20 | `templates/emails/marketing_contact_request.html` (80), `templates/emails/persistent_agent_email.html` (41), `templates/emails/gobii_base.html` (28), `templates/emails/organization_invite.html` (23) |
| Blog templates | 205 | 0.1% | 2 | `templates/blog/index.html` (126), `templates/blog/detail.html` (79) |

### Vendor and reference UI (13,758 LOC, 71 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Preline reference components | 13,758 | 4.3% | 71 | `vendor/preline/fullpage-1.html` (1,722), `vendor/preline/ai-chat-3-with-sidebar.html` (1,299), `vendor/preline/pricing-1.html` (1,266), `vendor/preline/ai-chat-2.html` (936) |

### Sandbox, infrastructure, and operations (10,263 LOC, 65 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Django project configuration | 3,800 | 1.2% | 18 | `config/settings.py` (1,643), `config/urls.py` (784), `config/redis_client.py` (338), `config/allauth_adapter.py` (193) |
| Sandbox server | 2,113 | 0.7% | 17 | `sandbox_server/server/sync.py` (464), `sandbox_server/server/mcp.py` (350), `sandbox_server/server/run.py` (260), `sandbox_server/server/workspace.py` (249) |
| Developer and maintenance scripts | 1,491 | 0.5% | 8 | `scripts/check_complexity_budgets.py` (536), `scripts/debug_captcha_solver.py` (283), `scripts/check_test_tags.py` (188), `scripts/budgets/complexity_budgets.json` (154) |
| CI workflow configuration | 1,088 | 0.3% | 6 | `.github/workflows/ci.yml` (394), `.github/workflows/docs.yml` (370), `.github/workflows/release.yml` (187), `.github/workflows/droid-implement.yml` (69) |
| Docker and deployment configuration | 1,003 | 0.3% | 10 | `compose.yaml` (339), `docker-compose.dev.yaml` (323), `docker/Dockerfile` (163), `docker/bootstrap/runtime_env.py` (117) |
| Project entrypoints and observability | 763 | 0.2% | 5 | `observability.py` (333), `celery_task_counter.py` (247), `pyproject.toml` (152), `manage.py` (17) |
| Developer tooling configuration | 5 | 0.0% | 1 | `.factory/settings.json` (5) |

### Utilities and examples (7,942 LOC, 52 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Shared utility modules | 1,969 | 0.6% | 15 | `util/text_sanitizer.py` (521), `util/sms.py` (265), `util/ephemeral_xvfb.py` (219), `util/user_behavior.py` (174) |
| Setup wizard app | 1,528 | 0.5% | 8 | `setup/views.py` (562), `setup/templates/setup/wizard.html` (538), `setup/forms.py` (256), `setup/templates/setup/db_error.html` (97) |
| Example clients and diagnostics | 1,262 | 0.4% | 7 | `misc/example_api_client.py` (672), `misc/ts-example-client/src/index.ts` (417), `misc/ts-example-client/sample-usage.ts` (65), `misc/ts-example-client/package.json` (44) |
| Top-level task service helpers | 1,129 | 0.4% | 2 | `tasks/services.py` (1,128), `tasks/__init__.py` (1) |
| Pretrained worker definitions | 896 | 0.3% | 2 | `agents/pretrained_worker_definitions.py` (532), `agents/services.py` (364) |
| Django middleware | 407 | 0.1% | 3 | `middleware/app_shell.py` (334), `middleware/user_id_baggage.py` (46), `middleware/console_timezone.py` (27) |
| Standalone helpers | 367 | 0.1% | 2 | `agent_namer.py` (258), `turnstile_signup.py` (109) |
| Shared constants | 296 | 0.1% | 8 | `constants/feature_flags.py` (127), `constants/security.py` (86), `constants/plans.py` (46), `constants/phone_countries.py` (19) |
| Global template tags | 88 | 0.0% | 5 | `templatetags/social_extras.py` (48), `templatetags/vite_tags.py` (22), `templatetags/analytics_tags.py` (10), `templatetags/form_extras.py` (8) |

### Billing and subscription backend (4,007 LOC, 15 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Billing and trial utilities | 2,234 | 0.7% | 5 | `util/subscription_helper.py` (1,479), `config/stripe_config.py` (420), `util/trial_eligibility.py` (186), `util/trial_enforcement.py` (118) |
| Billing domain services | 1,773 | 0.6% | 10 | `billing/addons.py` (683), `billing/plan_resolver.py` (237), `billing/checkout_metadata.py` (190), `billing/lifecycle_handlers.py` (155) |

### Static source assets (3,182 LOC, 15 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Static JS, CSS, and manifest | 3,182 | 1.0% | 15 | `static/js/account_auth_forms.js` (713), `static/js/cta_signup_modal.js` (469), `static/js/agent_email_oauth.js` (443), `static/js/cta_tracking.js` (239) |

### Analytics, attribution, and marketing events (2,627 LOC, 19 files)

| Subfeature | LOC | % of total | Files | Representative files |
|---|---:|---:|---:|---|
| Marketing event providers and telemetry | 1,377 | 0.4% | 15 | `marketing_events/tasks.py` (351), `marketing_events/api.py` (179), `marketing_events/custom_events.py` (167), `marketing_events/providers/google_analytics.py` (132) |
| Analytics attribution helpers and middleware | 1,250 | 0.4% | 4 | `util/analytics.py` (722), `middleware/utm_capture.py` (235), `util/attribution_referrers.py` (213), `middleware/fbp_middleware.py` (80) |

## Largest Files

The full per-file inventory is in `reports/codebase_line_allocation_files.csv`.

| Rank | File | Feature | Subfeature | LOC |
|---:|---|---|---|---:|
| 1 | `api/models.py` | Backend platform and data model | Django domain models and schema history | 12,042 |
| 2 | `frontend/src/styles/agentChatLegacy.css` | Frontend console app | Frontend styling | 7,724 |
| 3 | `console/api_views.py` | Server-rendered console | General console views and forms | 7,417 |
| 4 | `api/agent/core/event_processing.py` | Agent runtime backend | Run loop, prompts, LLM config, result handling | 5,864 |
| 5 | `api/admin.py` | Backend platform and data model | Django admin surfaces | 5,512 |
| 6 | `api/agent/core/prompt_context.py` | Agent runtime backend | Run loop, prompts, LLM config, result handling | 4,537 |
| 7 | `frontend/src/screens/AgentChatPage.tsx` | Frontend console app | Route-level screens | 3,951 |
| 8 | `pages/views.py` | Public site, marketing, and templates | Public pages Django app | 3,908 |
| 9 | `pages/signals.py` | Public site, marketing, and templates | Public pages Django app | 3,830 |
| 10 | `frontend/src/screens/AgentDetailScreen.tsx` | Frontend console app | Route-level screens | 3,468 |
| 11 | `console/views.py` | Server-rendered console | General console views and forms | 3,224 |
| 12 | `api/agent/tools/mcp_manager.py` | Agent runtime backend | Tool registry, MCP, native/custom tools | 2,846 |
| 13 | `api/agent/core/result_analysis.py` | Agent runtime backend | Run loop, prompts, LLM config, result handling | 2,335 |
| 14 | `console/agent_settings/service.py` | Server-rendered console | Agent settings services and mutations | 2,242 |
| 15 | `frontend/src/components/agentChat/AgentComposer.tsx` | Frontend console app | Agent chat workspace components | 2,096 |
| 16 | `api/tasks/browser_agent_tasks.py` | Backend platform and data model | Celery and periodic task implementations | 2,045 |
| 17 | `api/agent/tools/meta_gobii.py` | Agent runtime backend | General agent tool implementations | 2,034 |
| 18 | `api/services/sandbox_compute.py` | Backend platform and data model | Sandbox and compute services | 2,011 |
| 19 | `api/services/remote_mcp.py` | Backend platform and data model | Integrations and MCP services | 1,995 |
| 20 | `api/agent/tools/meta_ads.py` | Agent runtime backend | General agent tool implementations | 1,825 |
| 21 | `api/agent/comms/human_input_requests.py` | Agent runtime backend | Email, SMS, web chat, and human-input communications | 1,804 |
| 22 | `frontend/src/components/agentChat/AgentChatLayout.tsx` | Frontend console app | Agent chat workspace components | 1,794 |
| 23 | `vendor/preline/fullpage-1.html` | Vendor and reference UI | Preline reference components | 1,722 |
| 24 | `pages/templates/home.html` | Public site, marketing, and templates | Public pages templates | 1,655 |
| 25 | `config/settings.py` | Sandbox, infrastructure, and operations | Django project configuration | 1,643 |
| 26 | `api/services/sandbox_kubernetes.py` | Backend platform and data model | Sandbox and compute services | 1,635 |
| 27 | `api/agent/tools/sqlite_batch.py` | Agent runtime backend | Agent data analysis and summarization tools | 1,627 |
| 28 | `frontend/src/components/tooling/toolMetadata.ts` | Frontend console app | Tool metadata and live tool UI | 1,574 |
| 29 | `frontend/src/components/agentChat/ToolClusterLivePreview.tsx` | Frontend console app | Agent chat workspace components | 1,505 |
| 30 | `util/subscription_helper.py` | Billing and subscription backend | Billing and trial utilities | 1,479 |
| 31 | `console/agent_chat/timeline.py` | Server-rendered console | Agent chat realtime/timeline server support | 1,409 |
| 32 | `api/agent/core/llm_config.py` | Agent runtime backend | Run loop, prompts, LLM config, result handling | 1,398 |
| 33 | `api/agent/tools/search_tools.py` | Agent runtime backend | Tool registry, MCP, native/custom tools | 1,395 |
| 34 | `api/agent/tools/tool_manager.py` | Agent runtime backend | Tool registry, MCP, native/custom tools | 1,375 |
| 35 | `api/agent/core/agent_judge.py` | Agent runtime backend | Run loop, prompts, LLM config, result handling | 1,351 |
| 36 | `vendor/preline/ai-chat-3-with-sidebar.html` | Vendor and reference UI | Preline reference components | 1,299 |
| 37 | `vendor/preline/pricing-1.html` | Vendor and reference UI | Preline reference components | 1,266 |
| 38 | `api/agent/comms/outbound_delivery.py` | Agent runtime backend | Email, SMS, web chat, and human-input communications | 1,247 |
| 39 | `api/agent/tools/sqlite_analysis.py` | Agent runtime backend | Agent data analysis and summarization tools | 1,210 |
| 40 | `frontend/src/screens/AgentEmailSettingsScreen.tsx` | Frontend console app | Route-level screens | 1,163 |
| 41 | `api/agent/tools/custom_tools.py` | Agent runtime backend | Tool registry, MCP, native/custom tools | 1,144 |
| 42 | `tasks/services.py` | Utilities and examples | Top-level task service helpers | 1,128 |
| 43 | `api/services/native_integrations.py` | Backend platform and data model | Integrations and MCP services | 1,095 |
| 44 | `api/agent/tools/sqlite_guardrails.py` | Agent runtime backend | Agent data analysis and summarization tools | 1,051 |
| 45 | `frontend/src/components/llmConfig/useRoutingTierActions.tsx` | Frontend console app | LLM configuration UI | 1,050 |
| 46 | `api/views.py` | Backend platform and data model | API views, serializers, webhooks, auth, and routing | 1,012 |
| 47 | `api/agent/tools/json_goldilocks.py` | Agent runtime backend | Agent data analysis and summarization tools | 996 |
| 48 | `frontend/src/screens/AgentAuditScreen.tsx` | Frontend console app | Route-level screens | 988 |
| 49 | `frontend/src/screens/StaffUsersScreen.tsx` | Frontend console app | Route-level screens | 988 |
| 50 | `frontend/src/components/mcp/McpServerFormModal.tsx` | Frontend console app | MCP and integrations UI components | 984 |
| 51 | `frontend/src/styles/insights.css` | Frontend console app | Frontend styling | 976 |
| 52 | `api/agent/tools/sqlite_state.py` | Agent runtime backend | Agent data analysis and summarization tools | 965 |
| 53 | `proprietary/views.py` | Public site, marketing, and templates | Proprietary marketing app | 953 |
| 54 | `api/services/discord_bot.py` | Backend platform and data model | Integrations and MCP services | 937 |
| 55 | `vendor/preline/ai-chat-2.html` | Vendor and reference UI | Preline reference components | 936 |
| 56 | `frontend/src/components/mcp/PipedreamAppsModal.tsx` | Frontend console app | MCP and integrations UI components | 935 |
| 57 | `frontend/src/screens/OrganizationScreen.tsx` | Frontend console app | Route-level screens | 923 |
| 58 | `frontend/src/components/llmConfig/shared.tsx` | Frontend console app | LLM configuration UI | 918 |
| 59 | `vendor/preline/ai-chat-1.html` | Vendor and reference UI | Preline reference components | 914 |
| 60 | `frontend/src/stores/agentChatStore.ts` | Frontend console app | Client state stores | 906 |
| 61 | `api/agent/tools/sqlite_digest.py` | Agent runtime backend | Agent data analysis and summarization tools | 897 |
| 62 | `api/webhooks.py` | Backend platform and data model | API views, serializers, webhooks, auth, and routing | 879 |
| 63 | `frontend/src/screens/ImmersiveApp.tsx` | Frontend console app | Route-level screens | 878 |
| 64 | `api/admin_forms.py` | Backend platform and data model | Django admin surfaces | 869 |
| 65 | `api/services/pipedream_trigger_subscriptions.py` | Backend platform and data model | Integrations and MCP services | 866 |
| 66 | `api/agent/comms/message_service.py` | Agent runtime backend | Email, SMS, web chat, and human-input communications | 858 |
| 67 | `console/usage_views.py` | Server-rendered console | Billing, usage, and credit console endpoints | 828 |
| 68 | `vendor/preline/table-2-users.html` | Vendor and reference UI | Preline reference components | 826 |
| 69 | `api/agent/system_skills/defaults.py` | Agent runtime backend | System skill definitions and defaults | 817 |
| 70 | `api/agent/tools/context_hints.py` | Agent runtime backend | General agent tool implementations | 808 |
| 71 | `console/email_settings/views.py` | Server-rendered console | Email settings | 798 |
| 72 | `config/urls.py` | Sandbox, infrastructure, and operations | Django project configuration | 784 |
| 73 | `frontend/src/screens/billing/BillingScreen.tsx` | Frontend console app | Route-level screens | 783 |
| 74 | `console/billing_update_service.py` | Server-rendered console | Billing, usage, and credit console endpoints | 766 |
| 75 | `templates/solutions/sales.html` | Public site, marketing, and templates | Solution landing page templates | 765 |
| 76 | `api/agent/core/tool_results.py` | Agent runtime backend | Run loop, prompts, LLM config, result handling | 758 |
| 77 | `templates/solutions/engineering.html` | Public site, marketing, and templates | Solution landing page templates | 754 |
| 78 | `templates/solutions/recruiting.html` | Public site, marketing, and templates | Solution landing page templates | 751 |
| 79 | `frontend/src/api/agentChat.ts` | Frontend console app | Frontend API clients | 731 |
| 80 | `util/analytics.py` | Analytics, attribution, and marketing events | Analytics attribution helpers and middleware | 722 |
| 81 | `console/organization_api_views.py` | Server-rendered console | Organization console endpoints | 715 |
| 82 | `api/agent/tools/sqlite_skills.py` | Agent runtime backend | Agent data analysis and summarization tools | 713 |
| 83 | `api/services/referral_service.py` | Backend platform and data model | Billing, credits, trials, and growth services | 713 |
| 84 | `static/js/account_auth_forms.js` | Static source assets | Static JS, CSS, and manifest | 713 |
| 85 | `api/services/agent_debug_trace.py` | Backend platform and data model | Agent lifecycle and configuration services | 697 |
| 86 | `billing/addons.py` | Billing and subscription backend | Billing domain services | 683 |
| 87 | `api/agent/tools/http_request.py` | Agent runtime backend | Tool registry, MCP, native/custom tools | 681 |
| 88 | `templates/solutions/health-care.html` | Public site, marketing, and templates | Solution landing page templates | 676 |
| 89 | `misc/example_api_client.py` | Utilities and examples | Example clients and diagnostics | 672 |
| 90 | `console/system_status.py` | Server-rendered console | General console views and forms | 668 |
| 91 | `console/forms.py` | Server-rendered console | General console views and forms | 667 |
| 92 | `console/llm_serializers.py` | Server-rendered console | General console views and forms | 650 |
| 93 | `api/agent/tools/create_video.py` | Agent runtime backend | Content and file generation tools | 648 |
| 94 | `api/serializers.py` | Backend platform and data model | API views, serializers, webhooks, auth, and routing | 642 |
| 95 | `api/agent/tasks/process_events.py` | Agent runtime backend | Agent task orchestration and peer communication | 631 |
| 96 | `templates/solutions/defense.html` | Public site, marketing, and templates | Solution landing page templates | 631 |
| 97 | `frontend/src/components/agentChat/insights/AgentSetupInsight.tsx` | Frontend console app | Agent chat workspace components | 629 |
| 98 | `frontend/src/components/homepage/HomepageIntegrationsModal.tsx` | Frontend console app | Other frontend components | 627 |
| 99 | `api/agent/browser_actions/captcha_solver.py` | Agent runtime backend | Browser action execution | 621 |
| 100 | `console/agent_chat/signals.py` | Server-rendered console | Agent chat realtime/timeline server support | 611 |

## Reading Notes

- `api/agent/**` is the main agent runtime: LLM prompting/run-loop code, tool implementations, MCP/custom tool plumbing, data-analysis helpers, browser/file handlers, and communication workflows.
- `frontend/src/**` is the React console app. Most frontend LOC is in agent chat, route screens, styling, MCP/integration UI, and LLM configuration.
- `api/models.py` plus `api/migrations/**` is large because it includes both current domain models and historical schema migrations. Keep that separate from active behavior when estimating feature implementation weight.
- `vendor/preline/**` is reference UI material, not first-party product logic, but it is tracked source and therefore listed separately rather than hidden.
- Django templates are split between the public marketing surface, auth/account/email flows, server-rendered console pages, and admin overrides.
