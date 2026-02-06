export type AgentRosterEntry = {
  id: string
  name: string
  avatarUrl: string | null
  displayColorHex: string | null
  isActive: boolean
  miniDescription: string
  isOrgOwned?: boolean
  isCollaborator?: boolean
  canManageAgent?: boolean
  canManageCollaborators?: boolean
  preferredLlmTier?: string | null
  email?: string | null
  sms?: string | null
}
