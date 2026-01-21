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
  canManageBilling?: boolean
}

export type AgentAddonsResponse = {
  contactCap?: ContactCapInfo | null
  status?: {
    contactCap?: ContactCapStatus | null
  }
  contactPacks?: ContactPackSettings | null
  plan?: {
    id?: string | null
    name?: string | null
    isFree?: boolean
  } | null
  upgradeUrl?: string | null
}

export type AgentAddonsUpdatePayload = {
  contactPacks: {
    quantities: Record<string, number>
  }
}
