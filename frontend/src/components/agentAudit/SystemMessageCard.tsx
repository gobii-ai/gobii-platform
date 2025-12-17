import type { ReactNode } from 'react'
import { Megaphone, Pencil } from 'lucide-react'
import type { AuditSystemMessageEvent } from '../../types/agentAudit'

type SystemMessageCardProps = {
  message: AuditSystemMessageEvent
  onEdit?: (message: AuditSystemMessageEvent) => void
  renderBody?: (body: string) => ReactNode
}

export function SystemMessageCard({ message, onEdit, renderBody }: SystemMessageCardProps) {
  const statusLabel = message.delivered_at ? 'Delivered' : 'Queued'
  const statusClass = message.delivered_at
    ? 'bg-emerald-50 text-emerald-700 ring-emerald-200'
    : 'bg-amber-50 text-amber-700 ring-amber-200'

  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-full bg-amber-50 text-amber-700">
            <Megaphone className="h-4 w-4" aria-hidden />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-900">System message</div>
            <div className="text-xs text-slate-600">{message.timestamp ? new Date(message.timestamp).toLocaleString() : '—'}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-600">
              {message.created_by?.name || message.created_by?.email ? (
                <span>by {message.created_by?.name || message.created_by?.email}</span>
              ) : null}
              {message.broadcast_id ? <span className="rounded-md bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-700">Broadcast</span> : null}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-2 py-1 text-[11px] font-semibold ring-1 ${statusClass}`}>{statusLabel}</span>
          {onEdit ? (
            <button
              type="button"
              onClick={() => onEdit(message)}
              className="inline-flex items-center gap-1 rounded-md bg-slate-900 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-slate-800"
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
              Edit
            </button>
          ) : null}
        </div>
      </div>
      <div className="mt-2">
        {renderBody ? (
          renderBody(message.body)
        ) : (
          <div className="whitespace-pre-wrap break-words rounded-md bg-amber-50/60 px-3 py-2 text-sm text-slate-900 shadow-inner shadow-amber-200/60">
            {message.body}
          </div>
        )}
      </div>
    </div>
  )
}
