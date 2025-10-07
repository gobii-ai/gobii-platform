// noinspection BadExpressionStatementJS
"use no memo"
import { useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  type Column,
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'

import type {
  DateRangeValue,
  UsageAgentLeaderboardQueryInput,
  UsageAgentLeaderboardResponse,
} from './types'
import { fetchUsageAgentLeaderboard } from './api'

type LeaderboardRow = {
  id: string
  name: string
  tasksTotal: number
  tasksPerDay: number
  successCount: number
  errorCount: number
  successRate: number | null
}

type UsageAgentLeaderboardProps = {
  effectiveRange: DateRangeValue | null
  fallbackRange: DateRangeValue | null
  agentIds: string[]
}

function SortIndicator({ state }: { state: false | 'asc' | 'desc' }) {
  const baseClasses = 'h-3 w-3 text-slate-400'

  if (state === 'asc') {
    return (
      <svg
        className={baseClasses}
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M4.5 9.5 8 6 11.5 9.5" />
      </svg>
    )
  }

  if (state === 'desc') {
    return (
      <svg
        className={baseClasses}
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M4.5 6.5 8 10 11.5 6.5" />
      </svg>
    )
  }

  return (
    <svg
      className={`${baseClasses} opacity-0`}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4.5 9.5 8 6 11.5 9.5" />
    </svg>
  )
}

type SortableHeaderProps = {
  column: Column<LeaderboardRow, unknown>
  align?: 'left' | 'right'
  children: ReactNode
}

function SortableHeader({ column, align = 'left', children }: SortableHeaderProps) {
  const isSorted = column.getIsSorted()

  const handleClick = () => {
    if (!column.getCanSort()) {
      return
    }
    column.toggleSorting()
  }

  const alignmentClass = align === 'right' ? 'justify-end text-right' : 'justify-start text-left'

  return (
    <button
      type="button"
      onClick={handleClick}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          handleClick()
        }
      }}
      className={`flex w-full items-center gap-2 text-xs font-medium uppercase tracking-wider text-slate-500 ${alignmentClass}`}
    >
      <span>{children}</span>
      <SortIndicator state={isSorted} />
    </button>
  )
}

export function UsageAgentLeaderboard({ effectiveRange, fallbackRange, agentIds }: UsageAgentLeaderboardProps) {
  const baseRange = effectiveRange ?? fallbackRange

  const integerFormatter = useMemo(() => new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }), [])
  const decimalFormatter = useMemo(
    () => new Intl.NumberFormat(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 }),
    [],
  )
  const percentFormatter = useMemo(
    () => new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1, minimumFractionDigits: 0 }),
    [],
  )

  const [sorting, setSorting] = useState<SortingState>([{ id: 'tasksTotal', desc: true }])

  const queryInput = useMemo<UsageAgentLeaderboardQueryInput | null>(() => {
    if (!baseRange) {
      return null
    }
    return {
      from: baseRange.start.toString(),
      to: baseRange.end.toString(),
      agents: agentIds,
    }
  }, [agentIds, baseRange])

  const agentKey = agentIds.length ? agentIds.slice().sort().join(',') : 'all'

  const {
    data,
    error,
    isError,
    isPending,
  } = useQuery<UsageAgentLeaderboardResponse, Error>({
    queryKey: ['usage-agent-leaderboard', queryInput?.from ?? null, queryInput?.to ?? null, agentKey],
    queryFn: ({ signal }) => fetchUsageAgentLeaderboard(queryInput!, signal),
    enabled: Boolean(queryInput),
    refetchOnWindowFocus: false,
    placeholderData: (previous) => previous,
  })

  const rows = useMemo<LeaderboardRow[]>(() => {
    if (!data) {
      return []
    }

    return data.agents
      .filter((agent) => Number(agent.tasks_total ?? 0) > 0)
      .map((agent) => {
        const tasksTotal = Number(agent.tasks_total ?? 0)
        const tasksPerDay = Number(agent.tasks_per_day ?? 0)
        const successCount = Number(agent.success_count ?? 0)
        const errorCount = Number(agent.error_count ?? 0)
        const attempts = successCount + errorCount
        const successRate = attempts > 0 ? successCount / attempts : null

        return {
          id: agent.id,
          name: agent.name || 'Unnamed agent',
          tasksTotal,
          tasksPerDay,
          successCount,
          errorCount,
          successRate,
        }
      })
  }, [data])

  const columns = useMemo<ColumnDef<LeaderboardRow>[]>(() => {
    return [
      {
        accessorKey: 'name',
        enableSorting: true,
        header: ({ column }) => <SortableHeader column={column}>Agent</SortableHeader>,
        cell: ({ row, getValue }) => {
          const label = getValue<string>()
          return (
            <div className="flex flex-col gap-0.5">
              <span className="text-sm font-medium text-slate-900">{label}</span>
              <span className="text-xs text-slate-500">#{row.index + 1}</span>
            </div>
          )
        },
      },
      {
        id: 'tasksTotal',
        accessorFn: (row) => row.tasksTotal,
        enableSorting: true,
        header: ({ column }) => (
          <SortableHeader column={column} align="right">
            Tasks
          </SortableHeader>
        ),
        cell: ({ getValue }) => {
          const value = Number(getValue<number>())
          return <span className="whitespace-nowrap text-sm font-semibold text-slate-900">{integerFormatter.format(value)}</span>
        },
        sortingFn: 'basic',
      },
      {
        id: 'tasksPerDay',
        accessorFn: (row) => row.tasksPerDay,
        enableSorting: true,
        header: ({ column }) => (
          <SortableHeader column={column} align="right">
            Avg / Day
          </SortableHeader>
        ),
        cell: ({ getValue }) => {
          const value = Number(getValue<number>())
          return <span className="whitespace-nowrap text-sm text-slate-900">{decimalFormatter.format(value)}</span>
        },
        sortingFn: 'basic',
      },
      {
        id: 'successRate',
        accessorFn: (row) => (row.successRate ?? -1),
        enableSorting: true,
        header: ({ column }) => (
          <SortableHeader column={column} align="right">
            Success / Error
          </SortableHeader>
        ),
        cell: ({ row }) => {
          const { successCount, errorCount, successRate } = row.original
          const total = successCount + errorCount
          if (total === 0) {
            return <span className="text-sm text-slate-500">—</span>
          }

          const ratioLabel = `${integerFormatter.format(successCount)} : ${integerFormatter.format(errorCount)}`
          const successLabel =
            successRate != null ? `${percentFormatter.format(successRate)} success` : undefined

          return (
            <div className="flex flex-col items-end gap-0.5 text-right">
              <span className="text-sm font-semibold text-slate-900">{ratioLabel}</span>
              {successLabel ? <span className="text-xs text-slate-500">{successLabel}</span> : null}
            </div>
          )
        },
        sortingFn: 'basic',
      },
    ]
  }, [decimalFormatter, integerFormatter, percentFormatter])

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: (row) => row.id,
  })

  return (
    <section className="rounded-xl border border-white/60 bg-white/80 shadow-xl">
      <header className="border-b border-slate-200/70 px-6 py-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold text-slate-900">Agent leaderboard</h2>
          <p className="text-sm text-slate-600">Ranked by task volume for the selected period.</p>
        </div>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full divide-y divide-slate-200/70">
          <thead className="bg-slate-50/50">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const sortState = header.column.getIsSorted()
                  const ariaSort = sortState === 'asc' ? 'ascending' : sortState === 'desc' ? 'descending' : 'none'
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={ariaSort}
                      className={`${header.column.id === 'name' ? 'text-left' : 'text-right'} px-3 md:px-6 py-3`}
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  )
                })}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-slate-200/70">
            {!queryInput ? (
              <tr>
                <td className="px-3 md:px-6 py-4 text-center text-sm text-slate-500" colSpan={columns.length}>
                  Select a date range to view agent performance.
                </td>
              </tr>
            ) : isPending ? (
              <tr>
                <td className="px-3 md:px-6 py-4 text-center text-sm text-slate-500" colSpan={columns.length}>
                  Loading agent activity…
                </td>
              </tr>
            ) : isError ? (
              <tr>
                <td className="px-3 md:px-6 py-4 text-center text-sm text-red-600" colSpan={columns.length}>
                  {error?.message || 'Unable to load agent leaderboard right now.'}
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td className="px-3 md:px-6 py-4 text-center text-sm text-slate-500" colSpan={columns.length}>
                  No agent activity yet.
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr key={row.id} className="bg-white/70">
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={`${cell.column.id === 'name' ? 'text-left' : 'text-right'} px-3 md:px-6 py-4 align-middle`}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
