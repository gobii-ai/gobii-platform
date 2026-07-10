import type { BillingOrgData } from './types'
import { QuantityStepper } from './QuantityStepper'

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
        <QuantityStepper
          value={seatTarget}
          onDecrease={() => onAdjust(-1)}
          onIncrease={() => onAdjust(1)}
          decreaseDisabled={!canManage || saving || seatTarget <= minSeats}
          increaseDisabled={!canManage || saving}
          decreaseLabel="Decrease seats"
          increaseLabel="Increase seats"
          incrementTone="neutral"
          decrementDisabledOpacity="50"
          incrementDisabledOpacity="50"
          valueClassName="min-w-[3.75rem] rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-center text-base font-bold text-slate-900 tabular-nums"
        />
      </div>
    </div>
  )
}
