import type { ReactNode } from 'react'

type EventHeaderProps = {
  left: ReactNode
  right?: ReactNode
  collapsed?: boolean
  onToggle?: () => void
  className?: string
}

const collapseButtonClassName =
  'rounded-md bg-slate-900 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-slate-800'

export function EventHeader({ left, right, collapsed = false, onToggle, className }: EventHeaderProps) {
  const classes = ['flex items-start justify-between gap-3', className].filter(Boolean).join(' ')

  return (
    <div className={classes}>
      <div className="flex items-start gap-3">{left}</div>
      <div className="flex items-center gap-2">
        {right}
        {onToggle ? (
          <button type="button" onClick={onToggle} className={collapseButtonClassName}>
            {collapsed ? 'Expand' : 'Collapse'}
          </button>
        ) : null}
      </div>
    </div>
  )
}
