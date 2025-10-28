import type { ReactNode } from 'react'
import { isRecord } from '../../util/objectUtils'

type JsonValue = null | boolean | number | string | JsonValue[] | Record<string, unknown>

type RenderContext = {
  depth: number
  maxDepth: number
  seen: WeakSet<object>
}

type StructuredDataTableProps = {
  value: unknown
  className?: string
  maxDepth?: number
}

function formatPrimitive(value: unknown): ReactNode {
  if (value === null) return <span className="italic text-slate-400">null</span>
  if (value === undefined) return <span className="italic text-slate-400">undefined</span>
  if (typeof value === 'string') {
    if (value.trim().length === 0) {
      return <span className="italic text-slate-400">(empty)</span>
    }
    return <span className="font-mono text-slate-700 break-words">{value}</span>
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      return <span className="font-mono text-slate-700 break-words">{String(value)}</span>
    }
    return <span className="font-mono text-slate-700 break-words">{value.toLocaleString()}</span>
  }
  if (typeof value === 'boolean') {
    return <span className="font-mono text-slate-700 break-words">{value ? 'true' : 'false'}</span>
  }
  if (typeof value === 'bigint') {
    return <span className="font-mono text-slate-700 break-words">{value.toString()}n</span>
  }
  if (value instanceof Date) {
    return <span className="font-mono text-slate-700 break-words">{value.toISOString()}</span>
  }
  return <span className="font-mono text-slate-700 break-words">{String(value)}</span>
}

function renderArray(values: JsonValue[], context: RenderContext): ReactNode {
  if (context.depth >= context.maxDepth) {
    return <span className="italic text-slate-400">Array({values.length})</span>
  }

  if (!values.length) {
    return <span className="italic text-slate-400">Empty list</span>
  }

  if (context.seen.has(values)) {
    return <span className="italic text-rose-500">[Circular]</span>
  }
  context.seen.add(values)

  const nextContext: RenderContext = { ...context, depth: context.depth + 1 }
  const rows = values.map((item, index) => ({
    index,
    content: renderValue(item, nextContext),
  }))

  return (
    <div className="space-y-3 text-xs text-slate-600">
      <table className="hidden w-full table-fixed border-collapse text-left sm:table">
        <thead>
          <tr className="bg-slate-100 text-slate-600">
            <th className="w-16 border border-slate-200 px-3 py-2 font-semibold">#</th>
            <th className="border border-slate-200 px-3 py-2 font-semibold">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.index} className="align-top">
              <th className="border border-slate-200 bg-slate-50 px-3 py-2 font-semibold text-slate-600 align-top">#{row.index}</th>
              <td className="border border-slate-200 px-3 py-2">{row.content}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="space-y-2 sm:hidden">
        {rows.map((row) => (
          <div key={row.index} className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
            <div className="text-[0.7rem] font-semibold uppercase tracking-wide text-slate-500">#{row.index}</div>
            <div className="mt-1 text-xs text-slate-600">{row.content}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function renderObject(value: Record<string, unknown>, context: RenderContext): ReactNode {
  if (context.depth >= context.maxDepth) {
    return <span className="italic text-slate-400">Object({Object.keys(value).length})</span>
  }

  const entries = Object.entries(value)
  if (!entries.length) {
    return <span className="italic text-slate-400">Empty object</span>
  }

  if (context.seen.has(value)) {
    return <span className="italic text-rose-500">[Circular]</span>
  }
  context.seen.add(value)

  const nextContext: RenderContext = { ...context, depth: context.depth + 1 }
  const rows = entries.map(([key, child]) => ({
    key,
    content: renderValue(child as JsonValue, nextContext),
  }))

  return (
    <div className="space-y-3 text-xs text-slate-600">
      <table className="hidden w-full table-fixed border-collapse text-left sm:table">
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="align-top">
              <th className="border border-slate-200 bg-slate-50 px-3 py-2 font-semibold text-slate-600 align-top">{row.key}</th>
              <td className="border border-slate-200 px-3 py-2">{row.content}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="space-y-2 sm:hidden">
        {rows.map((row) => (
          <div key={row.key} className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
            <div className="text-[0.7rem] font-semibold uppercase tracking-wide text-slate-500">{row.key}</div>
            <div className="mt-1 text-xs text-slate-600">{row.content}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function renderValue(value: JsonValue, context: RenderContext): ReactNode {
  if (Array.isArray(value)) {
    return renderArray(value, { ...context, depth: context.depth + 1 })
  }
  if (isRecord(value)) {
    return renderObject(value, { ...context, depth: context.depth + 1 })
  }
  return formatPrimitive(value)
}

export function StructuredDataTable({ value, className, maxDepth = 8 }: StructuredDataTableProps) {
  const containerClasses = [
    'structured-data-table max-h-60 overflow-auto rounded-xl border border-slate-200 bg-slate-50 px-2 shadow-inner sm:px-3',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')

  const context: RenderContext = {
    depth: 0,
    maxDepth,
    seen: new WeakSet<object>(),
  }

  return (
    <div className={containerClasses}>
      <div className="min-w-[16rem]">{renderValue(value as JsonValue, context)}</div>
    </div>
  )
}
