import { HttpError, jsonFetch, jsonRequest } from './http'
import type { ConsoleContext } from './context'
import type { AgentRosterEntry, AgentRosterSortMode, AgentTransferInvite, PlanningState, SignupPreviewState } from '../types/agentRoster'
import type { AccountPauseInfo, BillingStatusInfo } from '../types/agentAddons'
import type { LlmIntelligenceConfig } from '../types/llmIntelligence'

export type UpdateAgentPayload = {
  preferred_llm_tier?: string
}

type CreateAgentPayload = {
  message: string
  preferred_llm_tier?: string
  charter_override?: string
  selected_pipedream_app_slugs?: string[]
  preferred_contact_method?: 'email' | 'web'
}

export type CreateAgentResponse = {
  agent_id: string
  agent_name: string
  agent_email?: string | null
  planning_state?: PlanningState | null
}

type AgentRosterPayload = {
  context: ConsoleContext
  requested_agent_status?: 'deleted' | 'missing' | null
  agent_roster_sort_mode?: AgentRosterSortMode
  favorite_agent_ids?: string[]
  muted_agent_ids?: string[]
  insights_panel_expanded?: boolean | null
  agent_chat_notifications_enabled?: boolean
  billingStatus?: BillingStatusInfo | null
  accountPause?: AccountPauseInfo | null
  llmIntelligence?: LlmIntelligenceConfig | null
  transfer_invites?: {
    id: string
    agent_id: string
    agent_name: string
    agent_avatar_url: string | null
    initiated_by_name: string
    initiated_by_email: string
    recipient_email: string
    message: string
    created_at: string | null
    created_at_display: string
    accept_url: string
    decline_url: string
  }[]
  agents: {
    id: string
    name: string
    avatar_url: string | null
    is_active: boolean
    processing_active: boolean
    mini_description: string
    short_description: string
    listing_description: string
    listing_description_source: string | null
    display_tags: string[]
    detail_url: string | null
    daily_credit_remaining: number | null
    daily_credit_low: boolean
    last_24h_credit_burn: number | null
    is_org_owned: boolean
    is_collaborator: boolean
    can_manage_agent: boolean
    can_manage_collaborators: boolean
    audit_url?: string | null
    preferred_llm_tier: string | null
    email: string | null
    sms: string | null
    last_interaction_at: string | null
    signup_preview_state?: SignupPreviewState | null
    planning_state?: PlanningState | null
    pending_action_request_count?: number
    has_unread_agent_message?: boolean
    latest_agent_message_id?: string | null
    latest_agent_message_at?: string | null
    latest_agent_message_read_at?: string | null
    enabled_system_skills?: string[]
  }[]
}

export async function fetchAgentRoster(
  options: { forAgentId?: string } = {},
): Promise<{
  context: ConsoleContext
  agents: AgentRosterEntry[]
  transferInvites: AgentTransferInvite[]
  agentRosterSortMode: AgentRosterSortMode
  favoriteAgentIds: string[]
  mutedAgentIds: string[]
  insightsPanelExpanded: boolean | null
  agentChatNotificationsEnabled: boolean
  requestedAgentStatus?: 'deleted' | 'missing' | null
  billingStatus?: BillingStatusInfo | null
  accountPause?: AccountPauseInfo | null
  llmIntelligence?: LlmIntelligenceConfig | null
}> {
  const query = options.forAgentId ? `?for_agent=${encodeURIComponent(options.forAgentId)}` : ''
  const payload = await jsonFetch<AgentRosterPayload>(`/console/api/agents/roster/${query}`)
  const agents = payload.agents.map((agent) => ({
    id: agent.id,
    name: agent.name,
    avatarUrl: agent.avatar_url,
    isActive: agent.is_active,
    processingActive: agent.processing_active,
    miniDescription: agent.mini_description,
    shortDescription: agent.short_description,
    listingDescription: agent.listing_description,
    listingDescriptionSource: agent.listing_description_source,
    displayTags: Array.isArray(agent.display_tags) ? agent.display_tags : [],
    detailUrl: agent.detail_url,
    dailyCreditRemaining: agent.daily_credit_remaining,
    dailyCreditLow: Boolean(agent.daily_credit_low),
    last24hCreditBurn: agent.last_24h_credit_burn,
    auditUrl: agent.audit_url ?? null,
    isOrgOwned: agent.is_org_owned,
    isCollaborator: agent.is_collaborator,
    canManageAgent: agent.can_manage_agent,
    canManageCollaborators: agent.can_manage_collaborators,
    preferredLlmTier: agent.preferred_llm_tier,
    email: agent.email,
    sms: agent.sms,
    lastInteractionAt: agent.last_interaction_at,
    signupPreviewState: agent.signup_preview_state ?? null,
    planningState: agent.planning_state ?? null,
    pendingActionRequestCount: Math.max(0, Number(agent.pending_action_request_count ?? 0) || 0),
    hasUnreadAgentMessage: Boolean(agent.has_unread_agent_message),
    latestAgentMessageId: agent.latest_agent_message_id ?? null,
    latestAgentMessageAt: agent.latest_agent_message_at ?? null,
    latestAgentMessageReadAt: agent.latest_agent_message_read_at ?? null,
    enabledSystemSkills: Array.isArray(agent.enabled_system_skills)
      ? agent.enabled_system_skills.filter((value): value is string => typeof value === 'string')
      : [],
  }))
  const transferInvites = (payload.transfer_invites ?? []).map((invite) => ({
    id: invite.id,
    agentId: invite.agent_id,
    agentName: invite.agent_name,
    agentAvatarUrl: invite.agent_avatar_url,
    initiatedByName: invite.initiated_by_name,
    initiatedByEmail: invite.initiated_by_email,
    recipientEmail: invite.recipient_email,
    message: invite.message,
    createdAt: invite.created_at,
    createdAtDisplay: invite.created_at_display,
    acceptUrl: invite.accept_url,
    declineUrl: invite.decline_url,
  }))
  return {
    context: payload.context,
    agents,
    transferInvites,
    agentRosterSortMode: payload.agent_roster_sort_mode ?? 'recent',
    favoriteAgentIds: Array.isArray(payload.favorite_agent_ids)
      ? payload.favorite_agent_ids.filter((value): value is string => typeof value === 'string')
      : [],
    mutedAgentIds: Array.isArray(payload.muted_agent_ids)
      ? payload.muted_agent_ids.filter((value): value is string => typeof value === 'string')
      : [],
    insightsPanelExpanded: payload.insights_panel_expanded ?? null,
    agentChatNotificationsEnabled: payload.agent_chat_notifications_enabled !== false,
    requestedAgentStatus: payload.requested_agent_status ?? null,
    billingStatus: payload.billingStatus ?? null,
    accountPause: payload.accountPause ?? null,
    llmIntelligence: payload.llmIntelligence,
  }
}

export type AgentTransferInviteActionResponse = {
  ok: boolean
  action: 'accept' | 'decline'
  message?: string
  agent?: {
    id: string
    name: string
    isActive: boolean
    detailUrl: string
    chatUrl: string
  } | null
}

function extractApiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof HttpError && error.body && typeof error.body === 'object') {
    const apiError = (error.body as { error?: unknown }).error
    if (typeof apiError === 'string' && apiError.trim()) {
      return apiError
    }
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return fallback
}

export async function respondToAgentTransferInvite(
  url: string,
): Promise<AgentTransferInviteActionResponse> {
  try {
    return await jsonRequest<AgentTransferInviteActionResponse>(url, {
      method: 'POST',
      includeCsrf: true,
    })
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Could not respond to the transfer invite.'))
  }
}

export function updateAgent(agentId: string, payload: UpdateAgentPayload): Promise<void> {
  return jsonRequest(`/console/api/agents/${agentId}/`, {
    method: 'PATCH',
    json: payload,
    includeCsrf: true,
  })
}

export async function createAgent(
  message: string,
  preferredLlmTier?: string,
  charterOverride?: string | null,
  selectedPipedreamAppSlugs?: string[],
  preferredContactMethod?: 'email' | 'web',
  attachments: File[] = [],
): Promise<CreateAgentResponse> {
  const payload: CreateAgentPayload = { message, preferred_llm_tier: preferredLlmTier }
  if (charterOverride) {
    payload.charter_override = charterOverride
  }
  if (selectedPipedreamAppSlugs && selectedPipedreamAppSlugs.length > 0) {
    payload.selected_pipedream_app_slugs = selectedPipedreamAppSlugs
  }
  if (preferredContactMethod) {
    payload.preferred_contact_method = preferredContactMethod
  }
  if (attachments.length > 0) {
    const formData = new FormData()
    formData.append('message', message)
    if (preferredLlmTier) {
      formData.append('preferred_llm_tier', preferredLlmTier)
    }
    if (charterOverride) {
      formData.append('charter_override', charterOverride)
    }
    selectedPipedreamAppSlugs?.forEach((slug) => {
      formData.append('selected_pipedream_app_slugs', slug)
    })
    if (preferredContactMethod) {
      formData.append('preferred_contact_method', preferredContactMethod)
    }
    attachments.forEach((file) => {
      formData.append('attachments', file)
    })
    return jsonFetch<CreateAgentResponse>('/console/api/agents/create/', {
      method: 'POST',
      body: formData,
    })
  }
  return jsonFetch<CreateAgentResponse>('/console/api/agents/create/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
