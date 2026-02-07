import type { BillingAddonContext, BillingAddonKindKey, BillingAddonOption, BillingPlan, Money } from './types'

export function normalizeCurrency(currency: string | null | undefined): string {
  const trimmed = (currency ?? '').trim()
  return trimmed ? trimmed.toUpperCase() : 'USD'
}

export function formatCents(amountCents: number, currency: string): string {
  const normalized = normalizeCurrency(currency)
  const amount = amountCents / 100
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: normalized,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount)
  } catch {
    return `${normalized} ${amount.toFixed(2)}`
  }
}

export function planMonthlyPriceCents(plan: BillingPlan): number {
  const raw = typeof plan.monthly_price === 'number' ? plan.monthly_price : typeof plan.price === 'number' ? plan.price : 0
  return Math.max(0, Math.round(raw * 100))
}

export function buildInitialAddonQuantityMap(addons: BillingAddonContext): Record<string, number> {
  const next: Record<string, number> = {}
  const keys: BillingAddonKindKey[] = ['taskPack', 'contactPack', 'browserTaskPack', 'advancedCaptcha']
  keys.forEach((key) => {
    const options = addons.kinds[key]?.options ?? []
    options.forEach((option) => {
      next[option.priceId] = option.quantity ?? 0
    })
  })
  return next
}

export function buildAddonOptionLabel(kind: BillingAddonKindKey, option: BillingAddonOption): string {
  const delta = option.delta ?? 0
  if (kind === 'taskPack') return `+${delta.toLocaleString()} tasks`
  if (kind === 'contactPack') return `+${delta.toLocaleString()} contacts`
  if (kind === 'browserTaskPack') return `+${delta.toLocaleString()} browser tasks/day`
  return `Advanced CAPTCHA`
}

export function resolveAddonLineItems(
  addons: BillingAddonContext,
  quantities: Record<string, number>,
): Array<{ id: string; label: string; money: Money }> {
  const items: Array<{ id: string; label: string; money: Money }> = []
  const keys: BillingAddonKindKey[] = ['taskPack', 'contactPack', 'browserTaskPack', 'advancedCaptcha']
  keys.forEach((kind) => {
    const options = addons.kinds[kind]?.options ?? []
    options.forEach((option) => {
      const qty = quantities[option.priceId] ?? 0
      if (qty <= 0) {
        return
      }
      const unitAmount = typeof option.unitAmount === 'number' ? option.unitAmount : 0
      const amountCents = unitAmount * qty
      const currency = normalizeCurrency(option.currency || addons.totals.currency)
      items.push({
        id: option.priceId,
        label: `${buildAddonOptionLabel(kind, option)} x ${qty}`,
        money: { amountCents, currency },
      })
    })
  })
  return items
}

