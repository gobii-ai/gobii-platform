import type { DailyCreditsInfo, DailyCreditsStatus, DailyCreditsUpdatePayload } from './dailyCredits'

export type ContactCapInfo = {
  limit: number | null
  used: number
  remaining: number | null
  active: number
  pending: number
  unlimited: boolean
}

export type ContactCapStatus = {
  limitReached: boolean
}

export type ContactPackOption = {
  priceId: string
  delta: number
  quantity: number
  unitAmount?: number | null
  currency?: string | null
  priceDisplay?: string | null
}

export type ContactPackSettings = {
  options: ContactPackOption[]
}

export type ContactPackMeta = {
  canManageBilling?: boolean
}

export type ContactPackUpdatePayload = {
  quantities: Record<string, number>
}

export type AgentQuickSettings = {
  dailyCredits?: DailyCreditsInfo | null
  contactCap?: ContactCapInfo | null
  contactPacks?: ContactPackSettings | null
}

export type AgentQuickSettingsStatus = {
  dailyCredits?: DailyCreditsStatus | null
  contactCap?: ContactCapStatus | null
}

export type AgentQuickSettingsResponse = {
  settings: AgentQuickSettings
  status: AgentQuickSettingsStatus
  meta?: {
    plan?: {
      id?: string | null
      name?: string | null
      isFree?: boolean
    } | null
    upgradeUrl?: string | null
    contactPacks?: ContactPackMeta | null
  }
}

export type AgentQuickSettingsUpdatePayload = {
  dailyCredits?: DailyCreditsUpdatePayload
  contactPacks?: ContactPackUpdatePayload
}
