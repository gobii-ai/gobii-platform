import { AlertTriangle } from 'lucide-react'

import type { AuditErrorEvent } from '../../types/agentAudit'
import { JsonBlock } from '../agentChat/toolDetails/shared'
import { EventHeader } from './EventHeader'
import { IconCircle } from './eventPrimitives'

export function ErrorRow({
  error,
  collapsed = false,
  onToggle,
}: {
  error: AuditErrorEvent
  collapsed?: boolean
  onToggle?: () => void
}) {
  const hasContext = error.context && Object.keys(error.context).length > 0
  const categoryLabel = (error.category || 'OTHER').replaceAll('_', ' ')

  return (
    <div className="rounded-lg border border-rose-200/90 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <EventHeader
        left={
          <>
            <IconCircle icon={AlertTriangle} bgClass="bg-rose-50" textClass="text-rose-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">{categoryLabel}</div>
              <div className="text-xs text-slate-600">{error.timestamp ? new Date(error.timestamp).toLocaleString() : '—'}</div>
            </div>
          </>
        }
        right={<span className="rounded-full bg-rose-50 px-2 py-1 text-[11px] font-semibold text-rose-700">{error.level || 'ERROR'}</span>}
        collapsed={collapsed}
        onToggle={onToggle}
      />
      {!collapsed ? (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap gap-2">
            {error.source ? <span className="rounded-full bg-rose-50 px-2 py-1 text-[11px] font-medium text-rose-700">{error.source}</span> : null}
            {error.exception_class ? <span className="rounded-full bg-rose-50 px-2 py-1 text-[11px] font-medium text-rose-700">{error.exception_class}</span> : null}
            {error.completion_id ? <span className="rounded-full bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700">Completion {error.completion_id}</span> : null}
          </div>
          {error.message ? <div className="whitespace-pre-wrap break-words text-sm text-slate-900">{error.message}</div> : null}
          {hasContext ? (
            <div className="space-y-1">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Context</div>
              <JsonBlock value={error.context} />
            </div>
          ) : null}
          {error.traceback ? (
            <details className="rounded-md border border-rose-100 bg-rose-50/60 px-3 py-2">
              <summary className="cursor-pointer text-xs font-semibold text-rose-700">Traceback</summary>
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words text-[12px] text-slate-900">{error.traceback}</pre>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
