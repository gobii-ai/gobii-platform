import { Slider, SliderThumb, SliderTrack } from 'react-aria-components'

import type { DailyCreditLimitMetrics, DailyCreditLimitValue } from './dailyCreditLimit'

type DailyCreditLimitControlProps = {
  id: string
  value: DailyCreditLimitValue
  metrics: DailyCreditLimitMetrics
  onSliderChange: (value: number) => void
  onInputChange: (value: string) => void
  surface?: 'standalone' | 'embedded'
  label: string
  helperText: string
  inputName?: string
  sliderInputName?: string
  disabled?: boolean
}

export function DailyCreditLimitControl({
  id,
  value,
  metrics,
  onSliderChange,
  onInputChange,
  surface = 'standalone',
  label,
  helperText,
  inputName,
  sliderInputName,
  disabled = false,
}: DailyCreditLimitControlProps) {
  const embedded = surface === 'embedded'
  const labelClassName = embedded ? 'text-slate-200' : 'text-slate-700'
  const mutedClassName = embedded ? 'text-slate-400' : 'text-slate-600'
  const trackClassName = embedded ? 'bg-slate-700' : 'bg-blue-100'
  const inputClassName = embedded
    ? 'border-slate-500/50 bg-slate-950/70 text-slate-100 placeholder:text-slate-500 focus:border-sky-400 focus:ring-sky-400/30'
    : 'border-slate-300 bg-white text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:ring-blue-500/25'

  return (
    <div className="space-y-3">
      <label id={`${id}-label`} htmlFor={`${id}-input`} className={`text-sm font-medium ${labelClassName}`}>
        {label}
      </label>
      {sliderInputName ? <input type="hidden" name={sliderInputName} value={value.sliderValue} /> : null}
      <Slider
        aria-labelledby={`${id}-label`}
        id={id}
        minValue={metrics.min}
        maxValue={metrics.max}
        step={metrics.step}
        value={value.sliderValue}
        onChange={(nextValue: number | number[]) => {
          const numeric = Array.isArray(nextValue) ? nextValue[0] : nextValue
          if (typeof numeric === 'number') onSliderChange(numeric)
        }}
        isDisabled={disabled}
        className="px-2 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <SliderTrack className={`relative h-2 rounded-full ${trackClassName}`}>
          {({ state }) => {
            const percent = Math.min(Math.max(state.getThumbPercent(0) * 100, 0), 100)
            return (
              <>
                <div className="absolute inset-y-0 left-0 rounded-full bg-sky-500" style={{ width: `${percent}%` }} />
                <SliderThumb
                  index={0}
                  className="absolute top-1/2 h-5 w-5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-blue-600 shadow-sm transition focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/30 data-[dragging]:scale-105"
                />
              </>
            )
          }}
        </SliderTrack>
      </Slider>
      <div className={`flex items-center justify-between text-xs font-medium ${mutedClassName}`}>
        <span>{value.sliderValue === metrics.emptyValue ? 'Unlimited' : `${Math.round(value.sliderValue).toLocaleString()} credits/day`}</span>
        <span>Unlimited</span>
      </div>
      <div className="flex items-center gap-2">
        <input
          id={`${id}-input`}
          name={inputName}
          type="number"
          step="1"
          min={metrics.min}
          max={metrics.limitMax}
          value={value.input}
          onChange={(event) => onInputChange(event.currentTarget.value)}
          disabled={disabled}
          className={`block w-full rounded-lg border px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-60 ${inputClassName}`}
          placeholder="Unlimited"
        />
        <span className={`shrink-0 text-sm ${mutedClassName}`}>credits/day</span>
      </div>
      <p className={`text-xs ${mutedClassName}`}>{helperText}</p>
    </div>
  )
}
