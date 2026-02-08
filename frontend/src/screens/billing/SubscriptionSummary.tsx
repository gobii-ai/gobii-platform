import { CreditCard } from 'lucide-react'

import type { BillingInitialData, Money } from './types'
import type { BillingDraftState } from './draft'
import { formatCents, normalizeCurrency, planMonthlyPriceCents, resolveAddonLineItems } from './utils'

type SubscriptionSummaryProps = {
  initialData: BillingInitialData
  draft: BillingDraftState
  showActions?: boolean
  saving?: boolean
  error?: string | null
  onSave?: () => void
  onCancel?: () => void
}

type SummaryItem = { id: string; label: string; money: Money }

export function SubscriptionSummary({
  initialData,
  draft,
  showActions = false,
  saving = false,
  error = null,
  onSave,
  onCancel,
}: SubscriptionSummaryProps) {
  const isOrg = initialData.contextType === 'organization'
  const planName = (initialData.plan?.name as string | undefined) ?? (isOrg ? 'Team' : 'Plan')
  const planCurrency = isOrg
    ? normalizeCurrency(initialData.seats.currency || (initialData.plan?.currency as string | undefined) || 'USD')
    : normalizeCurrency((initialData.plan?.currency as string | undefined) ?? 'USD')
  const basePriceCents = isOrg
    ? Math.max(0, Math.round((initialData.seats.unitPrice || 0) * 100))
    : planMonthlyPriceCents(initialData.plan)

  const seatTarget = isOrg ? (draft.seatTarget ?? initialData.seats.purchased) : 0

  const effectiveDedicatedCount = (() => {
    const current = initialData.dedicatedIps.proxies.length
    const removed = draft.dedicatedRemoveIds.length
    return Math.max(0, current + draft.dedicatedAddQty - removed)
  })()

  const summaryItems: SummaryItem[] = (() => {
    const items: SummaryItem[] = []
    if (isOrg) {
      items.push({
        id: 'seats',
        label: `${seatTarget} seat${seatTarget === 1 ? '' : 's'} (${formatCents(basePriceCents, planCurrency)}/seat)`,
        money: { amountCents: basePriceCents * seatTarget, currency: planCurrency },
      })
    } else {
      items.push({
        id: 'plan',
        label: planName,
        money: { amountCents: basePriceCents, currency: planCurrency },
      })
    }

    const addonItems = resolveAddonLineItems(initialData.addons, draft.addonQuantities)
    addonItems.forEach((item) => items.push(item))

    if (effectiveDedicatedCount > 0) {
      const unitCents = Math.max(0, Math.round((initialData.dedicatedIps.unitPrice || 0) * 100))
      const currency = normalizeCurrency(initialData.dedicatedIps.currency || planCurrency)
      items.push({
        id: 'dedicated-ips',
        label: `Dedicated IP${effectiveDedicatedCount === 1 ? '' : 's'} x ${effectiveDedicatedCount}`,
        money: { amountCents: unitCents * effectiveDedicatedCount, currency },
      })
    }

    return items
  })()

  const summaryTotal = (() => {
    const currency = planCurrency
    const amountCents = summaryItems.reduce((sum, item) => sum + item.money.amountCents, 0)
    return { amountCents, currency }
  })()

  return (
    <section className="card scroll-mt-28" data-section="billing-summary" id="billing-summary">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <CreditCard className="h-4 w-4 text-slate-500" />
          <span>Subscription Summary</span>
        </div>
      </div>

      <div className="mt-5 space-y-2">
        {summaryItems.map((item) => (
          <div
            key={item.id}
            className="flex flex-col gap-1 rounded-2xl border border-slate-200 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="text-sm font-semibold text-slate-900">{item.label}</div>
            <div className="text-sm font-bold text-slate-900 tabular-nums">
              {formatCents(item.money.amountCents, item.money.currency)}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-6 border-t border-slate-200 pt-4">
        <div className="flex items-center justify-between gap-3 rounded-2xl bg-slate-900 px-4 py-3 text-white">
          <div className="text-sm font-semibold">Total per month</div>
          <div className="text-lg font-extrabold tabular-nums">
            {formatCents(summaryTotal.amountCents, summaryTotal.currency)}
          </div>
        </div>
      </div>

      {showActions ? (
        <div className="mt-5 space-y-3" id="billing-summary-actions">
          {error ? (
            <div className="rounded-2xl border border-rose-200 bg-white px-4 py-3 text-sm font-semibold text-rose-700">
              {error}
            </div>
          ) : null}
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
            <button
              type="button"
              onClick={onCancel}
              disabled={saving || !onCancel}
              className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={saving || !onSave}
              className="inline-flex items-center justify-center rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-60"
            >
              {saving ? 'Saving…' : 'Update subscription'}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
