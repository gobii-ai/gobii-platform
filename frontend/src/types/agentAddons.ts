export type ContactCapInfo = {
  limit: number | null
  used: number
  remaining: number | null
  active: number
  pending: number
  unlimited: boolean
  channels?: {
    channel: string
    used: number
    limit: number | null
    remaining: number | null
  }[]
  periodStart?: string | null
  periodEnd?: string | null
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

export type AgentAddonsResponse = {
  contactCap?: ContactCapInfo | null
  status?: {
    contactCap?: ContactCapStatus | null
  }
  contactPacks?: ContactPackSettings | null
  taskPacks?: TaskPackSettings | null
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
