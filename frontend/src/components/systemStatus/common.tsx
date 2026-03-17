import type { ReactNode } from 'react'

import type { SystemStatusCard, SystemStatusLevel } from '../../types/systemStatus'

export type TableColumn<Row> = {
  key: string
  label: string
  render: (row: Row) => ReactNode
  align?: 'left' | 'right'
}

const statusTone: Record<SystemStatusLevel, string> = {
  healthy: 'bg-emerald-100 text-emerald-800 ring-1 ring-emerald-200',
  warning: 'bg-amber-100 text-amber-800 ring-1 ring-amber-200',
  critical: 'bg-rose-100 text-rose-800 ring-1 ring-rose-200',
  info: 'bg-sky-100 text-sky-800 ring-1 ring-sky-200',
}

const cardTone: Record<SystemStatusLevel, string> = {
  healthy: 'from-emerald-500/12 to-emerald-300/8 ring-1 ring-emerald-200/70',
  warning: 'from-amber-500/12 to-orange-300/8 ring-1 ring-amber-200/70',
  critical: 'from-rose-500/12 to-red-300/8 ring-1 ring-rose-200/70',
  info: 'from-sky-500/12 to-blue-300/8 ring-1 ring-sky-200/70',
}

export function formatDateTime(value: string): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '—'
  }
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

export function formatStatusLabel(value: string): string {
  if (!value) {
    return 'Unknown'
  }
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function StatusBadge({ status }: { status: SystemStatusLevel }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${statusTone[status]}`}>
      {formatStatusLabel(status)}
    </span>
  )
}

export function SummaryPill({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-full bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-50">
      <span className="text-slate-300">{label}:</span> {value}
    </div>
  )
}

export function MetricCard({ card }: { card: SystemStatusCard }) {
  return (
    <article className={`rounded-3xl bg-gradient-to-br ${cardTone[card.status]} p-5 shadow-[0_16px_40px_rgba(15,23,42,0.08)]`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <p className="text-sm font-semibold uppercase tracking-[0.12em] text-slate-600">{card.label}</p>
        <StatusBadge status={card.status} />
      </div>
      <div className="text-3xl font-semibold text-slate-950">{card.value}</div>
      <p className="mt-2 text-sm text-slate-600">{card.subtitle || 'Live snapshot'}</p>
    </article>
  )
}

export function DataTable<Row>({
  columns,
  rows,
  getRowKey,
}: {
  columns: TableColumn<Row>[]
  rows: Row[]
  getRowKey: (row: Row) => string
}) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-separate border-spacing-y-2">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                className={`px-3 pb-1 text-xs font-semibold uppercase tracking-[0.12em] text-slate-500 ${
                  column.align === 'right' ? 'text-right' : 'text-left'
                }`}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={getRowKey(row)} className="rounded-2xl bg-slate-50/80">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`px-3 py-3 text-sm text-slate-700 ${
                    column.align === 'right' ? 'text-right' : 'text-left'
                  }`}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function EmptyRows() {
  return (
    <div className="rounded-2xl bg-slate-50 px-4 py-5 text-sm text-slate-500">
      No rows to show right now.
    </div>
  )
}

export function UnavailableSection({ message }: { message?: string }) {
  return (
    <div className="rounded-2xl bg-rose-50 px-4 py-5 text-sm text-rose-700 ring-1 ring-rose-200">
      {message || 'Temporarily unavailable.'}
    </div>
  )
}

export function SectionCard({
  title,
  status,
  summary,
  children,
}: {
  title: string
  status: SystemStatusLevel
  summary: Array<{ label: string; value: ReactNode }>
  children: ReactNode
}) {
  return (
    <section className="card">
      <div className="card__header">
        <div>
          <h2 className="card__title">{title}</h2>
        </div>
        <StatusBadge status={status} />
      </div>
      <div className="flex flex-wrap gap-2">
        {summary.map((item) => (
          <SummaryPill key={item.label} label={item.label} value={item.value} />
        ))}
      </div>
      {children}
    </section>
  )
}

export function BooleanPill({ active, label }: { active: boolean; label: string }) {
  return (
    <span
      className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${
        active ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-200 text-slate-600'
      }`}
    >
      {label}
    </span>
  )
}
