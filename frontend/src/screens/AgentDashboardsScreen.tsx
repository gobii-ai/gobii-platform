import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Database,
  Gauge,
  RefreshCw,
  Table2,
} from 'lucide-react'

import { jsonFetch } from '../api/http'

type AgentDashboardPageAgent = {
  id: string
  name: string
  avatarUrl?: string | null
  displayColorHex?: string | null
  chatUrl?: string | null
}

type DashboardWidgetResult = {
  status: 'ok' | 'error'
  message?: string
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
  value?: unknown
}

type DashboardWidget = {
  id: string
  title: string
  type: 'metric' | 'table' | 'bar'
  position: number
  displayConfig: Record<string, unknown>
  result: DashboardWidgetResult
}

type AgentDashboard = {
  id: string
  title: string
  description: string
  createdByAgent: boolean
  createdAt: string | null
  updatedAt: string | null
  widgets: DashboardWidget[]
}

type AgentDashboardResponse = {
  agent: AgentDashboardPageAgent
  dashboard: AgentDashboard | null
}

export type AgentDashboardsScreenProps = {
  initialData: {
    agent: AgentDashboardPageAgent
    urls: {
      dashboard: string
      chat: string
    }
  }
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'No data'
  if (typeof value === 'number') {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

function numericValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value.replaceAll(',', ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />
        <span>{message}</span>
      </div>
    </div>
  )
}

function MetricWidget({ widget }: { widget: DashboardWidget }) {
  const value = widget.result.value ?? widget.result.rows[0]?.[widget.result.columns[0]]
  return (
    <section className="rounded-lg border border-cyan-200 bg-white p-5">
      <div className="mb-4 flex items-center gap-2 text-sm font-medium text-cyan-900">
        <Gauge className="h-4 w-4" aria-hidden="true" />
        <h2 className="m-0">{widget.title}</h2>
      </div>
      {widget.result.status === 'ok' ? (
        <div className="text-3xl font-semibold text-slate-950">{formatValue(value)}</div>
      ) : (
        <ErrorState message={widget.result.message || 'Unable to load this metric.'} />
      )}
    </section>
  )
}

function TableWidget({ widget }: { widget: DashboardWidget }) {
  const columns = widget.result.columns
  const rows = widget.result.rows
  return (
    <section className="rounded-lg border border-cyan-200 bg-white">
      <div className="flex items-center gap-2 px-5 py-4 text-sm font-medium text-cyan-900">
        <Table2 className="h-4 w-4" aria-hidden="true" />
        <h2 className="m-0">{widget.title}</h2>
      </div>
      {widget.result.status !== 'ok' ? (
        <div className="px-5 pb-5">
          <ErrorState message={widget.result.message || 'Unable to load this table.'} />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full border-t border-cyan-100 text-left text-sm">
            <thead className="bg-cyan-50 text-cyan-950">
              <tr>
                {columns.map((column) => (
                  <th key={column} scope="col" className="px-4 py-3 font-semibold">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-cyan-100">
              {rows.length === 0 ? (
                <tr>
                  <td className="px-4 py-5 text-slate-700" colSpan={Math.max(columns.length, 1)}>
                    No rows
                  </td>
                </tr>
              ) : (
                rows.map((row, rowIndex) => (
                  <tr key={`${widget.id}-${rowIndex}`} className="align-top">
                    {columns.map((column) => (
                      <td key={column} className="max-w-[22rem] px-4 py-3 text-slate-800">
                        <span className="line-clamp-3 break-words">{formatValue(row[column])}</span>
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
          {widget.result.truncated ? (
            <div className="border-t border-cyan-100 px-4 py-3 text-xs font-medium text-cyan-900">
              Results truncated
            </div>
          ) : null}
        </div>
      )}
    </section>
  )
}

function BarWidget({ widget }: { widget: DashboardWidget }) {
  const xColumn = typeof widget.displayConfig.x === 'string' ? widget.displayConfig.x : widget.result.columns[0]
  const yColumn = typeof widget.displayConfig.y === 'string' ? widget.displayConfig.y : widget.result.columns[1]
  const rows = widget.result.rows
  const values = rows.map((row) => numericValue(row[yColumn])).filter((value): value is number => value !== null)
  const maxValue = Math.max(...values, 0)

  return (
    <section className="rounded-lg border border-cyan-200 bg-white p-5">
      <div className="mb-5 flex items-center gap-2 text-sm font-medium text-cyan-900">
        <BarChart3 className="h-4 w-4" aria-hidden="true" />
        <h2 className="m-0">{widget.title}</h2>
      </div>
      {widget.result.status !== 'ok' ? (
        <ErrorState message={widget.result.message || 'Unable to load this chart.'} />
      ) : rows.length === 0 ? (
        <div className="text-sm text-slate-700">No rows</div>
      ) : (
        <div className="space-y-3">
          {rows.map((row, index) => {
            const value = numericValue(row[yColumn]) ?? 0
            const width = maxValue > 0 ? Math.max((value / maxValue) * 100, 2) : 0
            return (
              <div key={`${widget.id}-bar-${index}`} className="grid gap-1">
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate font-medium text-slate-800">{formatValue(row[xColumn])}</span>
                  <span className="flex-none tabular-nums text-slate-700">{formatValue(value)}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-cyan-50">
                  <div className="h-full rounded-full bg-cyan-600" style={{ width: `${width}%` }} />
                </div>
              </div>
            )
          })}
          {widget.result.truncated ? (
            <div className="pt-2 text-xs font-medium text-cyan-900">Results truncated</div>
          ) : null}
        </div>
      )}
    </section>
  )
}

function DashboardWidgetView({ widget }: { widget: DashboardWidget }) {
  if (widget.type === 'metric') return <MetricWidget widget={widget} />
  if (widget.type === 'bar') return <BarWidget widget={widget} />
  return <TableWidget widget={widget} />
}

export function AgentDashboardsScreen({ initialData }: AgentDashboardsScreenProps) {
  const dashboardQuery = useQuery({
    queryKey: ['agent-dashboard', initialData.agent.id],
    queryFn: ({ signal }) => jsonFetch<AgentDashboardResponse>(initialData.urls.dashboard, { signal }),
    refetchOnWindowFocus: false,
  })

  const payload = dashboardQuery.data
  const agent = payload?.agent ?? initialData.agent
  const dashboard = payload?.dashboard ?? null
  const widgets = useMemo(() => dashboard?.widgets ?? [], [dashboard])
  const chatUrl = agent.chatUrl || initialData.urls.chat
  const accent = agent.displayColorHex || '#0891b2'

  return (
    <main className="mx-auto flex max-w-7xl flex-col gap-6 px-1 py-2 text-slate-950">
      <header className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-cyan-200 bg-white p-5">
        <div className="flex min-w-0 items-center gap-4">
          <a
            href={chatUrl}
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-200 text-cyan-900 transition hover:bg-cyan-50"
            aria-label="Back to chat"
            title="Back to chat"
          >
            <ArrowLeft className="h-5 w-5" aria-hidden="true" />
          </a>
          <div className="flex min-w-0 items-center gap-3">
            {agent.avatarUrl ? (
              <img src={agent.avatarUrl} alt="" className="h-11 w-11 rounded-lg object-cover" />
            ) : (
              <div
                className="grid h-11 w-11 place-items-center rounded-lg text-sm font-semibold text-white"
                style={{ backgroundColor: accent }}
                aria-hidden="true"
              >
                {(agent.name || 'A').slice(0, 1).toUpperCase()}
              </div>
            )}
            <div className="min-w-0">
              <p className="m-0 truncate text-sm font-medium text-cyan-900">{agent.name}</p>
              <h1 className="m-0 truncate text-2xl font-semibold text-slate-950">
                {dashboard?.title || 'Dashboards'}
              </h1>
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void dashboardQuery.refetch()}
          className="inline-flex items-center gap-2 rounded-lg border border-cyan-200 bg-white px-4 py-2 text-sm font-semibold text-cyan-900 transition hover:bg-cyan-50 disabled:cursor-progress disabled:opacity-60"
          disabled={dashboardQuery.isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${dashboardQuery.isFetching ? 'animate-spin' : ''}`} aria-hidden="true" />
          Refresh
        </button>
      </header>

      {dashboardQuery.isError ? (
        <ErrorState message="Unable to load this dashboard." />
      ) : dashboardQuery.isPending ? (
        <div className="rounded-lg border border-cyan-200 bg-white p-8 text-sm text-cyan-950">Loading dashboard</div>
      ) : !dashboard ? (
        <section className="rounded-lg border border-cyan-200 bg-white p-8">
          <div className="flex max-w-xl items-start gap-4">
            <div className="grid h-11 w-11 flex-none place-items-center rounded-lg bg-cyan-50 text-cyan-700">
              <Database className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h2 className="m-0 text-lg font-semibold text-slate-950">No dashboard yet</h2>
              <p className="mt-2 text-sm leading-6 text-slate-700">
                This agent has not created a dashboard.
              </p>
              <a
                href={chatUrl}
                className="mt-4 inline-flex items-center gap-2 rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-cyan-800"
              >
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                Open chat
              </a>
            </div>
          </div>
        </section>
      ) : (
        <>
          {dashboard.description ? (
            <p className="m-0 max-w-3xl text-sm leading-6 text-slate-700">{dashboard.description}</p>
          ) : null}
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {widgets.map((widget) => (
              <div key={widget.id} className={widget.type === 'table' ? 'xl:col-span-2' : ''}>
                <DashboardWidgetView widget={widget} />
              </div>
            ))}
          </div>
        </>
      )}
    </main>
  )
}
