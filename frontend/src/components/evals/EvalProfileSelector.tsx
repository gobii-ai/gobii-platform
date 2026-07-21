import { Loader2, RefreshCcw } from 'lucide-react'
import { Button } from 'react-aria-components'

import { EvalSelect, type EvalSelectOption } from './EvalSelect'

export type EvalProfileStatus = 'loading' | 'ready' | 'error'

type EvalProfileSelectorProps = {
  label: string
  value: string
  options: EvalSelectOption[]
  status: EvalProfileStatus
  onChange: (value: string) => void
  onRetry: () => void
}

export function EvalProfileSelector({
  label,
  value,
  options,
  status,
  onChange,
  onRetry,
}: EvalProfileSelectorProps) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <div className="flex items-center gap-2">
        <EvalSelect
          ariaLabel={label}
          value={value}
          options={options}
          onChange={onChange}
          disabled={status !== 'ready'}
          className="normal-case tracking-normal"
        />
        {status === 'loading' ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-blue-700" role="status">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading
          </span>
        ) : null}
        {status === 'error' ? (
          <Button
            onPress={onRetry}
            className="inline-flex h-10 items-center gap-1 rounded-lg border border-rose-300 bg-white px-3 text-xs font-semibold text-rose-700 hover:border-rose-400 hover:text-rose-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/25"
          >
            <RefreshCcw className="h-3.5 w-3.5" /> Retry profiles
          </Button>
        ) : null}
      </div>
      {status === 'error' ? (
        <span className="text-xs font-medium text-amber-700" role="status">Using default routing. Retry to choose a profile.</span>
      ) : null}
    </div>
  )
}
