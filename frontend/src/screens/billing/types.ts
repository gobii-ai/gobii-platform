import type { ReactNode } from 'react'

export type BillingAddonKindKey = 'taskPack' | 'contactPack' | 'browserTaskPack' | 'advancedCaptcha'

export type BillingAddonOption = {
  priceId: string
  quantity: number
  delta: number
  unitAmount: number | null
  currency: string
  priceDisplay: string
}

export type BillingAddonContext = {
  kinds: Partial<Record<BillingAddonKindKey, { options: BillingAddonOption[] }>>
  totals: {
    amountCents: number
    currency: string
    amountDisplay: string
  }
}

export type BillingPlan = Record<string, unknown> & {
  id?: string
  name?: string
  currency?: string
  price?: number
  monthly_price?: number
}

export type DedicatedIpAssignedAgent = { id: string; name: string }

export type DedicatedIpProxy = {
  id: string
  label: string
  name: string
  staticIp: string | null
  host: string
  assignedAgents: DedicatedIpAssignedAgent[]
}

export type DedicatedIpContext = {
  allowed: boolean
  unitPrice: number
  currency: string
  multiAssign: boolean
  proxies: DedicatedIpProxy[]
}

export type BillingEndpoints = {
  updateUrl: string
  cancelSubscriptionUrl?: string
}

export type BillingTrial = {
  isTrialing: boolean
  trialEndsAtIso: string | null
}

export type BillingPersonalData = {
  contextType: 'personal'
  canManageBilling: boolean
  paidSubscriber: boolean
  plan: BillingPlan
  trial: BillingTrial
  periodStartDate?: string | null
  periodEndDate?: string | null
  cancelAt?: string | null
  cancelAtPeriodEnd: boolean
  addons: BillingAddonContext
  addonsDisabled: boolean
  dedicatedIps: DedicatedIpContext
  endpoints: BillingEndpoints
}

export type BillingOrgData = {
  contextType: 'organization'
  organization: { id: string; name: string }
  canManageBilling: boolean
  plan: BillingPlan
  trial: BillingTrial
  seats: {
    purchased: number
    reserved: number
    available: number
    unitPrice: number
    currency: string
    pendingQuantity: number | null
    pendingEffectiveAtIso: string | null
    hasStripeSubscription: boolean
  }
  addons: BillingAddonContext
  addonsDisabled: boolean
  dedicatedIps: DedicatedIpContext
  endpoints: BillingEndpoints
}

export type BillingInitialData = BillingPersonalData | BillingOrgData

export type BillingScreenProps = {
  initialData: BillingInitialData
}

export type Money = {
  amountCents: number
  currency: string
}

export type ConfirmDialogProps = {
  open: boolean
  title: string
  description?: ReactNode
  confirmLabel: string
  cancelLabel?: string
  icon?: ReactNode
  busy?: boolean
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
  footerNote?: ReactNode
  children?: ReactNode
}

export type ToggleSwitchProps = {
  checked: boolean
  disabled?: boolean
  label: string
  description?: ReactNode
  onChange: (checked: boolean) => void
}
