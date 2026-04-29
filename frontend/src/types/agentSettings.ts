import type { LlmIntelligenceConfig } from './llmIntelligence'

export type PrimaryEndpoint = {
  address: string
}

export type PendingTransfer = {
  toEmail: string
  createdAtIso: string
  createdAtDisplay: string
}

export type AgentOrganization = {
  id: string
  name: string
} | null

export type AgentSummary = {
  id: string
  name: string
  avatarUrl: string | null
  charter: string
  isActive: boolean
  createdAtDisplay: string
  pendingTransfer: PendingTransfer | null
  whitelistPolicy: string
  organization: AgentOrganization
  preferredLlmTier: string
  agentColorHex: string
}

export type AgentColorOption = {
  id: string
  name: string
  hex: string
}

export type AgentDailyCreditsInfo = {
  limit: number | null
  hardLimit: number | null
  usage: number
  remaining: number | null
  softRemaining: number | null
  unlimited: boolean
  percentUsed: number | null
  softPercentUsed: number | null
  nextResetIso: string | null
  nextResetLabel: string | null
  low: boolean
  sliderMin: number
  sliderMax: number
  sliderLimitMax: number
  sliderStep: number
  sliderValue: number
  sliderEmptyValue: number
  standardSliderLimit: number
}

export type DedicatedIpOption = {
  id: string
  label: string
  inUseElsewhere: boolean
  disabled: boolean
  assignedNames: string[]
}

export type DedicatedIpInfo = {
  total: number
  available: number
  multiAssign: boolean
  ownerType: 'organization' | 'user'
  selectedId: string | null
  options: DedicatedIpOption[]
  organizationName: string | null
}

export type AllowlistEntry = {
  id: string
  channel: string
  address: string
  allowInbound: boolean
  allowOutbound: boolean
}

export type AllowlistInvite = {
  id: string
  channel: string
  address: string
  allowInbound: boolean
  allowOutbound: boolean
}

export type AllowlistState = {
  show: boolean
  ownerEmail: string | null
  ownerPhone: string | null
  entries: AllowlistEntry[]
  pendingInvites: AllowlistInvite[]
  activeCount: number
  maxContacts: number | null
  pendingContactRequests: number
  emailVerified: boolean
}

export type CollaboratorEntry = {
  id: string
  userId: string
  email: string
  name: string
}

export type CollaboratorInvite = {
  id: string
  email: string
  invitedAtIso: string | null
  expiresAtIso: string | null
}

export type CollaboratorState = {
  entries: CollaboratorEntry[]
  pendingInvites: CollaboratorInvite[]
  activeCount: number
  pendingCount: number
  totalCount: number
  maxContacts: number | null
  canManage: boolean
}

export type McpServer = {
  id: string
  displayName: string
  description: string | null
  scope: string
  inherited: boolean
  assigned: boolean
}

export type PersonalMcpServer = {
  id: string
  displayName: string
  description: string | null
  assigned: boolean
}

export type McpServersInfo = {
  inherited: McpServer[]
  organization: McpServer[]
  personal: PersonalMcpServer[]
  showPersonalForm: boolean
  canManage: boolean
  manageUrl: string | null
}

export type PeerLinkCandidate = {
  id: string
  name: string
}

export type PeerLinkState = {
  creditsRemaining: number | null
  windowResetLabel: string | null
}

export type PeerLinkEntry = {
  id: string
  counterpartId: string | null
  counterpartName: string | null
  isEnabled: boolean
  messagesPerWindow: number
  windowHours: number
  featureFlag: string | null
  createdOnLabel: string
  state: PeerLinkState | null
}

export type PeerLinksInfo = {
  entries: PeerLinkEntry[]
  candidates: PeerLinkCandidate[]
  defaults: {
    messagesPerWindow: number
    windowHours: number
  }
}

export type AgentWebhook = {
  id: string
  name: string
  url: string
}

export type AgentInboundWebhook = {
  id: string
  name: string
  url: string
  isActive: boolean
  lastTriggeredAt: string | null
}

export type AgentSettingsReassignmentInfo = {
  enabled: boolean
  canReassign: boolean
  organizations: { id: string; name: string }[]
  assignedOrg: AgentOrganization
}

export type AgentSettingsUrls = {
  detail: string
  list: string
  chat: string
  secrets: string
  emailSettings: string
  manageFiles: string
  smsEnable: string | null
  contactRequests: string
  delete: string
  mcpServersManage: string | null
}

export type AgentSettingsData = {
  csrfToken: string
  urls: AgentSettingsUrls
  agent: AgentSummary
  agentColors: AgentColorOption[]
  primaryEmail: PrimaryEndpoint | null
  primarySms: PrimaryEndpoint | null
  dailyCredits: AgentDailyCreditsInfo
  dedicatedIps: DedicatedIpInfo
  allowlist: AllowlistState
  collaborators: CollaboratorState
  mcpServers: McpServersInfo
  peerLinks: PeerLinksInfo
  webhooks: AgentWebhook[]
  inboundWebhooks: AgentInboundWebhook[]
  features: {
    organizations: boolean
  }
  reassignment: AgentSettingsReassignmentInfo
  llmIntelligence: LlmIntelligenceConfig | null
}
