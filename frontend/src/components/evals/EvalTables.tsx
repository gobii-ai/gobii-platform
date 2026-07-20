import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { ColumnDef, PaginationState, RowSelectionState } from '@tanstack/react-table'
import { getCoreRowModel, getPaginationRowModel, useReactTable } from '@tanstack/react-table'
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2, Play, RefreshCcw } from 'lucide-react'

import type { EvalScenario, EvalSuite, EvalSuiteRun } from '../../api/evals'
import { TanStackTableShell } from '../common/TanStackTableShell'
import { RunTypeBadge, StatusBadge } from './Badges'

const headerCellClassName = 'px-4 py-2.5 text-left text-[11px] font-bold uppercase tracking-wider text-slate-500'
const cellClassName = 'px-4 py-3 align-middle'
const checkboxClassName = 'h-4 w-4 rounded border-slate-300 bg-white text-blue-600 focus:ring-blue-500 focus:ring-offset-0'

export type EvalCatalogStatus = 'loading' | 'ready' | 'error'

function TableFrame({
  children,
  label,
  showScrollHint = false,
}: {
  children: ReactNode
  label: string
  showScrollHint?: boolean
}) {
  return (
    <>
      {showScrollHint ? (
        <p className="border-b border-blue-100 bg-white px-4 py-2 text-xs font-medium text-blue-700 md:hidden">
          Swipe horizontally to see all columns.
        </p>
      ) : null}
      <div className="overflow-x-auto focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500" role="region" aria-label={label} tabIndex={0}>
        {children}
      </div>
    </>
  )
}

function tableShellProps() {
  return {
    tableClassName: 'min-w-full border-collapse text-sm',
    headClassName: 'bg-white',
    headerRowClassName: 'border-b border-blue-100',
    headerCellClassName,
    bodyClassName: 'bg-white',
    rowClassName: 'border-b border-blue-50 last:border-b-0 hover:bg-blue-50/40',
    cellClassName,
  }
}

type SuiteSelectionTableProps = {
  suites: EvalSuite[]
  selectedSuites: Set<string>
  status: EvalCatalogStatus
  onSelectionChange: (selected: Set<string>) => void
  onRetry: () => void
}

export function SuiteSelectionTable({ suites, selectedSuites, status, onSelectionChange, onRetry }: SuiteSelectionTableProps) {
  const rowSelection = useMemo<RowSelectionState>(
    () => Object.fromEntries(Array.from(selectedSuites, (slug) => [slug, true])),
    [selectedSuites],
  )

  const columns = useMemo<ColumnDef<EvalSuite>[]>(() => [
    {
      id: 'select',
      size: 44,
      header: ({ table }) => (
        <input
          type="checkbox"
          checked={table.getIsAllRowsSelected()}
          ref={(input) => {
            if (input) input.indeterminate = table.getIsSomeRowsSelected()
          }}
          onChange={table.getToggleAllRowsSelectedHandler()}
          className={checkboxClassName}
          aria-label="Select all eval suites"
        />
      ),
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={row.getIsSelected()}
          onChange={row.getToggleSelectedHandler()}
          onClick={(event) => event.stopPropagation()}
          className={checkboxClassName}
          aria-label={`Select ${row.original.slug}`}
        />
      ),
    },
    {
      id: 'suite',
      header: 'Suite',
      cell: ({ row }) => (
        <div className="min-w-72">
          <div className="font-mono text-sm font-semibold text-slate-900">{row.original.slug}</div>
          <div className="mt-0.5 line-clamp-1 text-xs text-slate-500">
            {row.original.description || 'No description provided.'}
          </div>
        </div>
      ),
    },
    {
      id: 'scenarios',
      header: 'Scenarios',
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-sm font-semibold tabular-nums text-slate-700">
          {row.original.scenario_slugs.length}
        </span>
      ),
    },
  ], [])

  const table = useReactTable({
    data: suites,
    columns,
    state: { rowSelection },
    onRowSelectionChange: (updater) => {
      const next = typeof updater === 'function' ? updater(rowSelection) : updater
      onSelectionChange(new Set(Object.entries(next).filter(([, selected]) => selected).map(([slug]) => slug)))
    },
    getCoreRowModel: getCoreRowModel(),
    getRowId: (suite) => suite.slug,
    enableRowSelection: true,
  })

  return (
    <TableFrame label="Eval suites">
      <TanStackTableShell
        table={table}
        bodyState={catalogBodyState(status, 'eval suites', onRetry)}
        emptyState={{ content: 'No suites registered.', cellClassName: 'px-4 py-10 text-center text-sm text-slate-500' }}
        getRowProps={(row) => ({
          onClick: () => row.toggleSelected(),
          className: `${row.getIsSelected() ? 'bg-blue-50/70' : ''} cursor-pointer`,
        })}
        {...tableShellProps()}
      />
    </TableFrame>
  )
}

type ScenarioCatalogTableProps = {
  scenarios: EvalScenario[]
  filterKey: string
  status: EvalCatalogStatus
  launchingScenarioSlug: string | null
  launchDisabled: boolean
  onLaunch: (scenario: EvalScenario) => void
  onRetry: () => void
}

export function ScenarioCatalogTable({
  scenarios,
  filterKey,
  status,
  launchingScenarioSlug,
  launchDisabled,
  onLaunch,
  onRetry,
}: ScenarioCatalogTableProps) {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 20 })

  useEffect(() => {
    setPagination((current) => current.pageIndex === 0 ? current : { ...current, pageIndex: 0 })
  }, [filterKey])

  const columns = useMemo<ColumnDef<EvalScenario>[]>(() => [
    {
      id: 'scenario',
      header: 'Scenario',
      cell: ({ row }) => (
        <div className="min-w-80 max-w-xl">
          <div className="font-mono text-sm font-semibold text-slate-900">{row.original.slug}</div>
          <div className="mt-0.5 line-clamp-1 text-xs text-slate-500">
            {row.original.description || 'No description provided.'}
          </div>
        </div>
      ),
    },
    {
      id: 'suites',
      header: 'Suites',
      cell: ({ row }) => (
        <span className="block max-w-48 truncate text-xs text-slate-600" title={row.original.suite_slugs.join(', ')}>
          {row.original.suite_slugs.length ? row.original.suite_slugs.join(', ') : '—'}
        </span>
      ),
    },
    {
      id: 'classification',
      header: 'Classification',
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1.5 whitespace-nowrap">
          <CatalogBadge tone="blue">{row.original.metadata.tier}</CatalogBadge>
          <CatalogBadge tone="teal">{row.original.metadata.category}</CatalogBadge>
        </div>
      ),
    },
    {
      id: 'tasks',
      header: 'Tasks',
      cell: ({ row }) => <span className="font-semibold tabular-nums text-slate-700">{row.original.task_count}</span>,
    },
    {
      id: 'cost',
      header: 'Cost / Runtime',
      cell: ({ row }) => (
        <div className="whitespace-nowrap text-xs text-slate-600">
          <div className="font-semibold capitalize text-slate-700">{row.original.metadata.cost_class}</div>
          <div>{row.original.metadata.expected_runtime}</div>
        </div>
      ),
    },
    {
      id: 'actions',
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row }) => {
        const isLaunching = launchingScenarioSlug === row.original.slug
        return (
          <div className="flex justify-end">
            <button
              type="button"
              className="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-bold text-white transition-colors hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
              onClick={() => onLaunch(row.original)}
              disabled={launchDisabled || Boolean(launchingScenarioSlug)}
              title={launchDisabled ? 'Routing profiles must finish loading before an eval can be launched.' : undefined}
            >
              {isLaunching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5 fill-current" />}
              Run
            </button>
          </div>
        )
      },
    },
  ], [launchDisabled, launchingScenarioSlug, onLaunch])

  const table = useReactTable({
    data: scenarios,
    columns,
    state: { pagination },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getRowId: (scenario) => scenario.slug,
  })

  const firstRow = scenarios.length ? pagination.pageIndex * pagination.pageSize + 1 : 0
  const lastRow = Math.min((pagination.pageIndex + 1) * pagination.pageSize, scenarios.length)

  return (
    <>
      <TableFrame label="Scenario catalog" showScrollHint>
        <TanStackTableShell
          table={table}
          bodyState={catalogBodyState(status, 'scenarios', onRetry)}
          emptyState={{ content: 'No scenarios match the current filters.', cellClassName: 'px-4 py-10 text-center text-sm text-slate-500' }}
          {...tableShellProps()}
        />
      </TableFrame>
      {scenarios.length > 0 ? (
        <div className="flex flex-col gap-2 border-t border-blue-100 bg-white px-4 py-3 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <span>Showing {firstRow}–{lastRow} of {scenarios.length}</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="inline-flex items-center gap-1 rounded-lg border border-blue-100 bg-white px-2.5 py-1.5 font-semibold text-slate-700 hover:border-blue-200 hover:text-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft className="h-3.5 w-3.5" /> Previous
            </button>
            <span className="font-medium text-slate-600">Page {pagination.pageIndex + 1} of {table.getPageCount()}</span>
            <button
              type="button"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="inline-flex items-center gap-1 rounded-lg border border-blue-100 bg-white px-2.5 py-1.5 font-semibold text-slate-700 hover:border-blue-200 hover:text-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Next <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ) : null}
    </>
  )
}

type RecentActivityTableProps = {
  suiteRuns: EvalSuiteRun[]
  loading: boolean
  error: string | null
  onRetry: () => void
}

export function RecentActivityTable({ suiteRuns, loading, error, onRetry }: RecentActivityTableProps) {
  const columns = useMemo<ColumnDef<EvalSuiteRun>[]>(() => [
    {
      id: 'eval',
      header: 'Eval',
      cell: ({ row }) => {
        const href = `/evals/${row.original.id}/`
        return (
          <a
            href={href}
            className="block min-w-56 max-w-xl rounded-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          >
          <div className="flex items-center gap-2">
            <span className="font-semibold text-slate-900 transition-colors group-hover:text-blue-700">
              {row.original.display_name || row.original.suite_slug}
            </span>
            {row.original.launcher_type === 'global_skill' ? (
              <CatalogBadge tone="emerald">Skill Eval</CatalogBadge>
            ) : null}
          </div>
          {row.original.launcher_type === 'global_skill' && row.original.skill_eval?.task_prompt ? (
            <div className="mt-0.5 line-clamp-1 text-xs text-slate-500">{row.original.skill_eval.task_prompt}</div>
          ) : null}
          <div className="mt-0.5 font-mono text-[11px] text-slate-400">{row.original.id.slice(0, 8)}</div>
          </a>
        )
      },
    },
    {
      id: 'type',
      header: 'Type',
      cell: ({ row }) => <RunTypeBadge runType={row.original.run_type} />,
    },
    {
      id: 'status',
      header: 'Status / Progress',
      cell: ({ row }) => (
        <div className="space-y-1.5 whitespace-nowrap">
          <StatusBadge status={row.original.status || 'pending'} />
          {row.original.run_totals ? (
            <div className="text-xs font-medium tabular-nums text-slate-500">
              {row.original.run_totals.completed}/{row.original.run_totals.total_runs} complete
            </div>
          ) : null}
        </div>
      ),
    },
    {
      id: 'pass',
      header: 'Avg Pass',
      cell: ({ row }) => (
        <div className="whitespace-nowrap">
          <div className="font-semibold text-slate-900">{formatPassRate(row.original.task_totals)}</div>
          {row.original.task_totals ? (
            <div className="text-xs tabular-nums text-slate-500">
              {row.original.task_totals.passed ?? 0}/{row.original.task_totals.completed ?? row.original.task_totals.total}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      id: 'started',
      header: 'Started / Duration',
      cell: ({ row }) => (
        <div className="whitespace-nowrap text-xs text-slate-600">
          <div>{formatTimestamp(row.original.started_at)}</div>
          <div className="mt-0.5 font-mono text-slate-400">{formatDuration(row.original.started_at, row.original.finished_at)}</div>
        </div>
      ),
    },
    {
      id: 'open',
      size: 36,
      header: () => <span className="sr-only">Open run</span>,
      cell: () => (
        <div className="flex justify-end" aria-hidden="true">
          <ChevronRight className="h-4 w-4 text-slate-300 transition-all group-hover:translate-x-0.5 group-hover:text-blue-600" />
        </div>
      ),
    },
  ], [])

  const table = useReactTable({
    data: suiteRuns,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (run) => run.id,
  })

  const errorContent = error ? (
    <div role="alert" className="flex flex-wrap items-center justify-center gap-2 text-rose-800">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span>{error}</span>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-1.5 rounded-lg border border-rose-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-rose-700 hover:border-rose-400 hover:text-rose-900 focus:outline-none focus:ring-2 focus:ring-rose-500"
      >
        <RefreshCcw className="h-3.5 w-3.5" /> Retry
      </button>
    </div>
  ) : null

  return (
    <TableFrame label="Recent eval activity" showScrollHint>
      <TanStackTableShell
        table={table}
        bodyState={loading && suiteRuns.length === 0 ? { content: 'Loading recent eval runs…', cellClassName: 'px-4 py-10 text-center text-sm text-slate-500' } : null}
        emptyState={errorContent
          ? { content: errorContent, cellClassName: 'px-4 py-8 text-center text-sm' }
          : { content: 'No historical runs yet. Launch one above.', cellClassName: 'px-4 py-10 text-center text-sm text-slate-500' }}
        leadingRows={errorContent && suiteRuns.length > 0 ? (columnCount) => (
          <tr>
            <td colSpan={columnCount} className="bg-rose-50 px-4 py-2.5 text-center text-sm">
              {errorContent}
            </td>
          </tr>
        ) : null}
        getRowProps={(row) => ({
          onClick: (event) => {
            if ((event.target as HTMLElement).closest('a, button, input, select, textarea')) return
            window.location.assign(`/evals/${row.original.id}/`)
          },
          className: 'group cursor-pointer',
        })}
        {...tableShellProps()}
      />
    </TableFrame>
  )
}

function catalogBodyState(status: EvalCatalogStatus, subject: string, onRetry: () => void) {
  if (status === 'ready') return null
  if (status === 'loading') {
    return {
      content: `Loading ${subject}…`,
      cellClassName: 'px-4 py-10 text-center text-sm text-slate-500',
    }
  }
  return {
    content: (
      <div className="flex flex-col items-center gap-3">
        <span>Unable to load {subject}.</span>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-500"
        >
          <RefreshCcw className="h-3.5 w-3.5" /> Retry
        </button>
      </div>
    ),
    cellClassName: 'px-4 py-10 text-center text-sm text-rose-700',
  }
}

function CatalogBadge({
  children,
  tone,
}: {
  children: string
  tone: 'blue' | 'teal' | 'emerald'
}) {
  const classes = {
    blue: 'bg-blue-50 text-blue-700 ring-blue-200',
    teal: 'bg-teal-50 text-teal-700 ring-teal-200',
    emerald: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ring-1 ${classes[tone]}`}>
      {children}
    </span>
  )
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`
}

function formatDuration(start: string | null | undefined, end: string | null | undefined) {
  if (!start || !end) return '—'
  const seconds = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000)
  return Number.isNaN(seconds) ? '—' : `${seconds}s`
}

function formatPassRate(taskTotals: EvalSuiteRun['task_totals'] | null | undefined) {
  if (!taskTotals || taskTotals.pass_rate == null) return '—'
  return `${Math.round(taskTotals.pass_rate * 100)}%`
}
