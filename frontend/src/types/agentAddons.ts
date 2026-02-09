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

export type AddonPackOption = {
  priceId: string
  delta: number
  quantity: number
  unitAmount?: number | null
  currency?: string | null
  priceDisplay?: string | null
}

export type ContactPackSettings = {
  options: AddonPackOption[]
  canManageBilling?: boolean
}

export type TaskPackSettings = {
  options: AddonPackOption[]
  canManageBilling?: boolean
}

export type TrialInfo = {
  isTrialing: boolean
  trialEndsAtIso: string | null
}

export type AgentAddonsResponse = {
  contactCap?: ContactCapInfo | null
  status?: {
    contactCap?: ContactCapStatus | null
  }
  contactPacks?: ContactPackSettings | null
  taskPacks?: TaskPackSettings | null
  trial?: TrialInfo | null
  plan?: {
    id?: string | null
    name?: string | null
    isFree?: boolean
    price?: number | null
    currency?: string | null
  } | null
  upgradeUrl?: string | null
  manageBillingUrl?: string | null
}

export type AgentAddonsUpdatePayload = {
  contactPacks?: {
    quantities: Record<string, number>
  }
  taskPacks?: {
    quantities: Record<string, number>
  }
}
