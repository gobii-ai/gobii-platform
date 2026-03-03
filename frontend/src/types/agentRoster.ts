export type AgentRosterSortMode = 'recent' | 'alphabetical'

export type AgentRosterEntry = {
  id: string
  name: string
  avatarUrl: string | null
  displayColorHex: string | null
  isActive: boolean
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
}
