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
  displayColorHex: string | null
  isActive: boolean
  processingActive: boolean
  lastInteractionAt: string | null
  miniDescription: string
  shortDescription: string
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
}
