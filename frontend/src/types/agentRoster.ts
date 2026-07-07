export type AgentRosterSortMode = 'recent' | 'alphabetical'

export type SignupPreviewState =
  | 'none'
  | 'awaiting_first_reply_pause'
  | 'awaiting_signup_completion'

export type PlanningState =
  | 'planning'
  | 'completed'
  | 'skipped'

export type AgentRosterEntry = {
  id: string
  name: string
  avatarUrl: string | null
  isActive: boolean
  processingActive: boolean
  lastInteractionAt: string | null
  miniDescription: string
  shortDescription: string
  listingDescription: string
  listingDescriptionSource: string | null
  displayTags: string[]
  detailUrl: string | null
  dailyCreditRemaining: number | null
  dailyCreditLow: boolean
  last24hCreditBurn: number | null
  auditUrl?: string | null
  isOrgOwned?: boolean
  isCollaborator?: boolean
  canManageAgent?: boolean
  canManageCollaborators?: boolean
  preferredLlmTier?: string | null
  email?: string | null
  sms?: string | null
  signupPreviewState?: SignupPreviewState | null
  planningState?: PlanningState | null
  pendingActionRequestCount?: number
  hasUnreadAgentMessage?: boolean
  latestAgentMessageId?: string | null
  latestAgentMessageAt?: string | null
  latestAgentMessageReadAt?: string | null
  enabledSystemSkills?: string[]
}

export type AgentTransferInvite = {
  id: string
  agent_id: string
  agent_name: string
  agent_avatar_url: string | null
  initiated_by_name: string
  initiated_by_email: string
  recipient_email: string
  message: string
  created_at: string | null
  accept_url: string
  decline_url: string
}
