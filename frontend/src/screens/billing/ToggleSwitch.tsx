import type { ToggleSwitchProps } from './types'

export function ToggleSwitch({ checked, disabled = false, label, description, onChange }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-disabled={disabled}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-left transition hover:border-blue-200 hover:bg-blue-50/40 disabled:opacity-60"
    >
      <div className="min-w-0">
        <div className="text-sm font-semibold text-slate-900">{label}</div>
        {description ? <div className="mt-1 text-xs text-slate-600">{description}</div> : null}
      </div>
      <span
        className={[
          'relative inline-flex h-7 w-12 flex-shrink-0 items-center rounded-full p-1 transition-colors',
          checked ? 'bg-blue-600 ring-1 ring-blue-700/40' : 'bg-blue-600/25 ring-1 ring-blue-500/40',
        ].join(' ')}
        aria-hidden="true"
      >
        <span
          className={[
            'inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform',
            checked ? 'translate-x-5' : 'translate-x-0',
          ].join(' ')}
        />
      </span>
    </button>
  )
}

