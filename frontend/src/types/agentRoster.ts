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
  emotion?: string | null
  emotionExpiresAt?: string | null
  isActive: boolean
  processingActive: boolean
  lastInteractionAt: string | null
  miniDescription: string
  shortDescription: string
  displayTags: string[]
  isCollaborator?: boolean
  canManageAgent?: boolean
  email?: string | null
  sms?: string | null
  signupPreviewState?: SignupPreviewState | null
  planningState?: PlanningState | null
  pendingActionRequestCount?: number
  hasUnreadAgentMessage?: boolean
  latestAgentMessageId?: string | null
  latestAgentMessageAt?: string | null
  latestAgentMessageReadAt?: string | null
}

export type AgentProfileDetails = {
  listingDescription: string
  listingDescriptionSource: string | null
  detailUrl: string | null
  dailyCreditRemaining: number | null
  dailyCreditLow: boolean
  last24hCreditBurn: number | null
  developerLiveChatUrl: string | null
  isOrgOwned: boolean
  canReactivateAgent: boolean
  canManageCollaborators: boolean
  canSendMessages: boolean
  preferredLlmTier: string | null
  enabledSystemSkills: string[]
}

export type AgentProfileEntry = AgentRosterEntry & AgentProfileDetails
export type ActiveAgentEntry = AgentRosterEntry & Partial<AgentProfileDetails>

export type AgentSidebarInvite = {
  id: string
  kind: 'transfer' | 'collaboration'
  agent_name: string
  agent_avatar_url: string | null
  sender_name: string
  sender_email: string
  message: string
  accept_url: string
  decline_url: string
}
