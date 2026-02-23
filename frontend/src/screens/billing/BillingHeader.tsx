import { Building2, CreditCard, ExternalLink, RotateCcw, ShieldAlert } from 'lucide-react'

import type { BillingInitialData } from './types'
import { formatCents, normalizeCurrency, planMonthlyPriceCents } from './utils'
import { SeatManager } from './SeatManager'

type BillingHeaderProps = {
  initialData: BillingInitialData
  onChangePlan?: () => void
  onCancel?: () => void
  onResume?: () => void
  onManageInStripe: () => void
  seatTarget?: number
  saving?: boolean
  onAdjustSeat?: (delta: number) => void
  onCancelScheduledSeatChange?: () => void
}

export function BillingHeader({
  initialData,
  onChangePlan,
  onCancel,
  onResume,
  onManageInStripe,
  seatTarget,
  saving = false,
  onAdjustSeat,
  onCancelScheduledSeatChange,
}: BillingHeaderProps) {
  const isOrg = initialData.contextType === 'organization'
  const planName = (initialData.plan?.name as string | undefined) ?? (isOrg ? 'Team' : 'Plan')
  const planCurrency = isOrg
    ? normalizeCurrency(initialData.seats.currency || (initialData.plan?.currency as string | undefined) || 'USD')
    : normalizeCurrency((initialData.plan?.currency as string | undefined) || 'USD')
  const basePriceCents = isOrg
    ? Math.max(0, Math.round((initialData.seats.unitPrice || 0) * 100))
    : planMonthlyPriceCents(initialData.plan)
  const isTrialing = initialData.trial?.isTrialing
  const trialEndsAtIso = initialData.trial?.trialEndsAtIso

  const trialEndsLabel = (() => {
    if (!trialEndsAtIso) return null
    const d = new Date(trialEndsAtIso)
    if (Number.isNaN(d.getTime())) return null
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(d)
  })()

  const pendingSeatLabel = (() => {
    if (!isOrg) return null
    if (initialData.seats.pendingQuantity === null || !initialData.seats.pendingEffectiveAtIso) {
      return null
    }
    const date = new Date(initialData.seats.pendingEffectiveAtIso)
    const effective = Number.isFinite(date.getTime())
      ? date.toLocaleDateString()
      : initialData.seats.pendingEffectiveAtIso
    return `Seats scheduled to change to ${initialData.seats.pendingQuantity} on ${effective}.`
  })()

  return (
    <section className="card" data-section="billing-plan">
      <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
            {isOrg ? <Building2 className="h-4 w-4 text-slate-500" /> : <CreditCard className="h-4 w-4 text-slate-500" />}
            <span>Base plan</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-2xl font-bold text-slate-900">{planName}</div>
            {isTrialing ? (
              <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">
                Free trial
              </span>
            ) : null}
          </div>
          {isOrg ? (
            <p className="text-sm text-slate-600">
              {formatCents(basePriceCents, planCurrency)} per seat per month.
            </p>
          ) : (
            <p className="text-sm text-slate-600">
              {formatCents(basePriceCents, planCurrency)} per month.
            </p>
          )}
          {isTrialing && trialEndsLabel ? (
            <p className="text-sm font-semibold text-amber-800">
              Trial ends {trialEndsLabel}.
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {initialData.contextType === 'personal' && onChangePlan ? (
            <button
              type="button"
              onClick={onChangePlan}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
            >
              {initialData.paidSubscriber ? 'Change plan' : 'Upgrade'}
            </button>
          ) : null}

          {initialData.contextType === 'personal'
            && initialData.paidSubscriber
            && !initialData.cancelAtPeriodEnd
            && onCancel ? (
              <button
                type="button"
                onClick={onCancel}
                className="inline-flex items-center justify-center gap-2 rounded-xl border border-rose-200 bg-white px-4 py-2.5 text-sm font-semibold text-rose-700 transition hover:border-rose-300 hover:text-rose-800 focus:outline-none focus:ring-2 focus:ring-rose-500/30"
              >
                <ShieldAlert className="h-4 w-4" />
                Cancel
              </button>
            ) : null}

          {initialData.endpoints.stripePortalUrl ? (
            <button
              type="button"
              onClick={onManageInStripe}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/30"
            >
              <ExternalLink className="h-4 w-4" />
              Change Payment Method
            </button>
          ) : null}

          {initialData.contextType === 'personal'
            && initialData.paidSubscriber
            && initialData.cancelAtPeriodEnd
            && onResume ? (
              <button
                type="button"
                onClick={onResume}
                className="inline-flex items-center justify-center gap-2 rounded-xl border border-emerald-200 bg-white px-4 py-2.5 text-sm font-semibold text-emerald-700 transition hover:border-emerald-300 hover:text-emerald-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/20"
              >
                <RotateCcw className="h-4 w-4" />
                Resume subscription
              </button>
            ) : null}

          {isOrg && typeof seatTarget === 'number' && onAdjustSeat && onCancelScheduledSeatChange ? (
            <div className="sm:self-center">
              <SeatManager
                initialData={initialData}
                seatTarget={seatTarget}
                canManage={initialData.canManageBilling}
                saving={saving}
                onAdjust={onAdjustSeat}
                onCancelScheduledChange={onCancelScheduledSeatChange}
                variant="inline"
              />
            </div>
          ) : null}
        </div>
      </div>

      {initialData.contextType === 'personal' ? (
        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Billing period</div>
            <div className="mt-1 text-sm font-semibold text-slate-900">
              {initialData.periodStartDate && initialData.periodEndDate
                ? `${initialData.periodStartDate} to ${initialData.periodEndDate}`
                : '—'}
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Renewal</div>
            <div className="mt-1 text-sm font-semibold text-slate-900">
              {initialData.cancelAtPeriodEnd && initialData.cancelAt
                ? `Cancels on ${initialData.cancelAt}`
                : (initialData.periodEndDate ?? '—')}
            </div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Status</div>
            <div className="mt-1 text-sm font-semibold text-slate-900">
              {isTrialing
                ? (trialEndsLabel ? `Trial until ${trialEndsLabel}` : 'Trial')
                : (initialData.paidSubscriber ? 'Active' : 'Free')}
            </div>
          </div>
        </div>
      ) : null}

      {isOrg && pendingSeatLabel && onCancelScheduledSeatChange ? (
        <div className="mt-6 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>{pendingSeatLabel}</div>
            <button
              type="button"
              onClick={onCancelScheduledSeatChange}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-amber-200 bg-white px-3 py-2 text-sm font-semibold text-amber-800 transition hover:border-amber-300 disabled:opacity-50"
              disabled={!initialData.canManageBilling || saving}
            >
              Cancel scheduled change
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
