import { Minus, Plus } from 'lucide-react'
import type { BillingOrgData } from './types'

type SeatManagerProps = {
  initialData: BillingOrgData
  seatTarget: number
  canManage: boolean
  saving: boolean
  onAdjust: (delta: number) => void
  onCancelScheduledChange: () => void
  variant?: 'default' | 'inline'
}

export function SeatManager({
  initialData,
  seatTarget,
  canManage,
  saving,
  onAdjust,
  onCancelScheduledChange,
  variant = 'default',
}: SeatManagerProps) {
  const minSeats = Math.max(0, initialData.seats.reserved)
  void onCancelScheduledChange

  return (
    <div className={variant === 'inline' ? 'flex items-center gap-3' : 'flex flex-col gap-3'}>
      <div className="flex items-center gap-2">
        <div className="text-sm font-semibold text-slate-700">Seats</div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onAdjust(-1)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-50"
            disabled={!canManage || saving || seatTarget <= minSeats}
            aria-label="Decrease seats"
          >
            <Minus className="h-4 w-4" strokeWidth={3} />
          </button>
          <div className="min-w-[3.75rem] rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-center text-base font-bold text-slate-900 tabular-nums">
            {seatTarget}
          </div>
          <button
            type="button"
            onClick={() => onAdjust(1)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-50"
            disabled={!canManage || saving}
            aria-label="Increase seats"
          >
            <Plus className="h-4 w-4" strokeWidth={3} />
          </button>
        </div>
      </div>
    </div>
  )
}
