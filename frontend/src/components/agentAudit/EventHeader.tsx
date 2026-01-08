import type { ReactNode } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

type EventHeaderProps = {
  left: ReactNode
  right?: ReactNode
  collapsed?: boolean
  onToggle?: () => void
  className?: string
}

const collapseButtonClassName =
  'inline-flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 text-slate-600 transition hover:border-slate-300 hover:text-slate-900'

export function EventHeader({ left, right, collapsed = false, onToggle, className }: EventHeaderProps) {
  const classes = ['flex items-start justify-between gap-3', className].filter(Boolean).join(' ')

  return (
    <div className={classes}>
      <div className="flex items-start gap-3">{left}</div>
      <div className="flex items-center gap-2">
        {right}
        {onToggle ? (
          <button
            type="button"
            onClick={onToggle}
            className={collapseButtonClassName}
            aria-label={collapsed ? 'Expand event' : 'Collapse event'}
          >
            {collapsed ? <ChevronDown className="h-4 w-4" aria-hidden /> : <ChevronUp className="h-4 w-4" aria-hidden />}
          </button>
        ) : null}
      </div>
    </div>
  )
}
