import { Minus, Plus } from 'lucide-react'

type QuantityStepperProps = {
  value: number
  onDecrease: () => void
  onIncrease: () => void
  decreaseDisabled?: boolean
  increaseDisabled?: boolean
  decreaseLabel: string
  increaseLabel: string
  incrementTone?: 'neutral' | 'primary'
  decrementDisabledOpacity?: '50' | '60'
  incrementDisabledOpacity?: '50' | '60'
  valueClassName?: string
}

const neutralButtonBaseClassName = 'inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 hover:text-slate-900'
const primaryButtonBaseClassName = 'inline-flex h-9 w-9 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm transition hover:bg-blue-700'
const defaultValueClassName = 'min-w-[3.25rem] rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-center text-sm font-bold text-slate-900 tabular-nums'

function withDisabledOpacity(className: string, opacity: '50' | '60') {
  return `${className} ${opacity === '50' ? 'disabled:opacity-50' : 'disabled:opacity-60'}`
}

export function QuantityStepper({
  value,
  onDecrease,
  onIncrease,
  decreaseDisabled = false,
  increaseDisabled = false,
  decreaseLabel,
  increaseLabel,
  incrementTone = 'primary',
  decrementDisabledOpacity = '60',
  incrementDisabledOpacity = '60',
  valueClassName = defaultValueClassName,
}: QuantityStepperProps) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={onDecrease}
        disabled={decreaseDisabled}
        className={withDisabledOpacity(neutralButtonBaseClassName, decrementDisabledOpacity)}
        aria-label={decreaseLabel}
      >
        <Minus className="h-4 w-4" strokeWidth={3} />
      </button>
      <div className={valueClassName}>{value}</div>
      <button
        type="button"
        onClick={onIncrease}
        disabled={increaseDisabled}
        className={withDisabledOpacity(incrementTone === 'neutral' ? neutralButtonBaseClassName : primaryButtonBaseClassName, incrementDisabledOpacity)}
        aria-label={increaseLabel}
      >
        <Plus className="h-4 w-4" strokeWidth={3} />
      </button>
    </div>
  )
}
