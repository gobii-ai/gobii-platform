import { Minus, Plus, Users } from 'lucide-react'
import type { BillingOrgData } from './types'

type SeatManagerProps = {
  initialData: BillingOrgData
  seatTarget: number
  canManage: boolean
  saving: boolean
  onAdjust: (delta: number) => void
  onCancelScheduledChange: () => void
}

export function SeatManager({
  initialData,
  seatTarget,
  canManage,
  saving,
  onAdjust,
  onCancelScheduledChange,
}: SeatManagerProps) {
  const seatPurchaseRequired = initialData.seats.purchased <= 0
  const minSeats = Math.max(0, initialData.seats.reserved)

  const pendingLabel = (() => {
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
    <div className="mt-6 flex flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Users className="h-4 w-4 text-slate-500" />
          <span>Seats</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onAdjust(-1)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-50"
            disabled={!canManage || saving || seatTarget <= minSeats}
            aria-label="Decrease seats"
          >
            <Minus className="h-4 w-4" strokeWidth={3} />
          </button>
          <div className="min-w-[5.5rem] rounded-xl border border-slate-200 bg-white px-4 py-2 text-center text-lg font-bold text-slate-900 tabular-nums">
            {seatTarget}
          </div>
          <button
            type="button"
            onClick={() => onAdjust(1)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-50"
            disabled={!canManage || saving}
            aria-label="Increase seats"
          >
            <Plus className="h-4 w-4" strokeWidth={3} />
          </button>
        </div>
      </div>

      <div className="grid gap-3 text-sm text-slate-600 sm:grid-cols-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Purchased</div>
          <div className="mt-1 font-semibold text-slate-900 tabular-nums">{initialData.seats.purchased}</div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Reserved</div>
          <div className="mt-1 font-semibold text-slate-900 tabular-nums">{initialData.seats.reserved}</div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Available</div>
          <div className="mt-1 font-semibold text-slate-900 tabular-nums">{initialData.seats.available}</div>
        </div>
      </div>

      {pendingLabel ? (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>{pendingLabel}</div>
            <button
              type="button"
              onClick={onCancelScheduledChange}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-amber-200 bg-white px-3 py-2 text-sm font-semibold text-amber-800 transition hover:border-amber-300"
              disabled={!canManage || saving}
            >
              Cancel scheduled change
            </button>
          </div>
        </div>
      ) : null}

      {seatPurchaseRequired ? (
        <div className="rounded-2xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          Add-ons and dedicated IPs are disabled until this org has at least one seat.
        </div>
      ) : null}
    </div>
  )
}

