// noinspection BadExpressionStatementJS
"use no memo"
import { useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { type Column, type ColumnDef, type SortingState, flexRender, getCoreRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table'

import type { DateRangeValue, UsageAgentLeaderboardQueryInput, UsageAgentLeaderboardResponse } from './types'
import { fetchUsageAgentLeaderboard } from './api'
import { handleAppAnchorClick } from '../../util/appNavigation'
import { getSettingsSurfaceClassName } from '../common/SettingsSurface'

const API_AGENT_ID = 'api'
const DEFAULT_VISIBLE_ROWS = 5

type LeaderboardRow = {
  id: string
  name: string
  tasksTotal: number
  tasksPerDay: number
  persistentId: string | null
  isApi: boolean
  isDeleted: boolean
}

type UsageAgentLeaderboardProps = {
  effectiveRange: DateRangeValue | null
  fallbackRange: DateRangeValue | null
  agentIds: string[]
}

function SortIndicator({ state }: { state: false | 'asc' | 'desc' }) {
  const baseClasses = 'h-3 w-3 text-slate-500'

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
      className={`flex w-full items-center gap-2 text-xs font-medium uppercase tracking-wider text-slate-400 ${alignmentClass}`}
    >
      <span>{children}</span>
      <SortIndicator state={isSorted} />
    </button>
  )
}

function LeaderboardAgentCell({
  row,
  rank,
}: {
  row: LeaderboardRow
  rank: number
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="inline-flex items-center gap-2 text-sm font-medium text-slate-100">
        <span>{row.name}</span>
        {row.isDeleted ? (
          <span className="rounded-full border border-rose-300/25 bg-rose-950/40 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rose-200">
            Deleted
          </span>
        ) : null}
      </span>
      <span className="text-xs text-slate-500">#{rank}</span>
    </div>
  )
}

export function UsageAgentLeaderboard({ effectiveRange, fallbackRange, agentIds }: UsageAgentLeaderboardProps) {
  const baseRange = effectiveRange ?? fallbackRange

  const creditFormatter = useMemo(
    () => new Intl.NumberFormat(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }),
    [],
  )

  const [sorting, setSorting] = useState<SortingState>([{ id: 'tasksTotal', desc: true }])
  const [showAllRows, setShowAllRows] = useState(false)

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

        return {
          id: agent.id,
          name: agent.name || 'Unnamed agent',
          tasksTotal,
          tasksPerDay,
          persistentId: agent.persistent_id ?? null,
          isApi: agent.id === API_AGENT_ID,
          isDeleted: Boolean(agent.is_deleted),
        }
      })
  }, [data])

  const columns = useMemo<ColumnDef<LeaderboardRow>[]>(() => {
    return [
      {
        accessorKey: 'name',
        enableSorting: true,
        header: ({ column }) => <SortableHeader column={column}>Agent</SortableHeader>,
        cell: ({ row, table }) => (
          <LeaderboardAgentCell
            row={row.original}
            rank={table.getRowModel().rows.indexOf(row) + 1}
          />
        ),
      },
      {
        id: 'tasksTotal',
        accessorFn: (row) => row.tasksTotal,
        enableSorting: true,
        header: ({ column }) => (
          <SortableHeader column={column} align="right">
            Credits
          </SortableHeader>
        ),
        cell: ({ getValue }) => {
          const value = Number(getValue<number>())
          return <span className="whitespace-nowrap text-sm font-semibold text-slate-100">{creditFormatter.format(value)}</span>
        },
        sortingFn: 'basic',
      },
      {
        id: 'tasksPerDay',
        accessorFn: (row) => row.tasksPerDay,
        enableSorting: true,
        header: ({ column }) => (
          <SortableHeader column={column} align="right">
            Credits / Day
          </SortableHeader>
        ),
        cell: ({ getValue }) => {
          const value = Number(getValue<number>())
          return <span className="whitespace-nowrap text-sm text-slate-200">{creditFormatter.format(value)}</span>
        },
        sortingFn: 'basic',
      },
      {
        id: 'actions',
        accessorFn: () => null,
        enableSorting: false,
        header: () => (
          <div className="flex w-full justify-end text-right text-xs font-medium uppercase tracking-wider text-slate-400">
            Actions
          </div>
        ),
        cell: ({ row }) => {
          if (row.original.isApi) {
            return <span className="text-sm text-slate-500">—</span>
          }

          const persistentId = row.original.persistentId
          if (!persistentId) {
            return <span className="text-sm text-slate-500">—</span>
          }

          const configureHref = `/app/agents/${persistentId}/settings`

          return (
            <a
              href={configureHref}
              onClick={(event) => handleAppAnchorClick(event, configureHref)}
              className="text-sm font-semibold text-sky-300 hover:text-sky-200"
            >
              Configure
            </a>
          )
        },
      },
    ]
  }, [creditFormatter])

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: (row) => row.id,
  })
  const sortedRows = table.getRowModel().rows
  const hasOverflowRows = sortedRows.length > DEFAULT_VISIBLE_ROWS
  const visibleRows = showAllRows ? sortedRows : sortedRows.slice(0, DEFAULT_VISIBLE_ROWS)

  const sectionClassName = getSettingsSurfaceClassName({ variant: 'embedded', roundedClassName: 'rounded-xl' })
  const headerClassName = 'px-6 py-4'
  const titleClassName = 'text-lg font-semibold text-slate-50'
  const subtitleClassName = 'text-sm text-slate-400'
  const tableClassName = 'w-full'
  const tableHeadClassName = 'bg-slate-950/40'
  const tableBodyClassName = 'divide-y divide-slate-200/10'
  const emptyCellClassName = 'px-3 md:px-6 py-4 text-center text-sm text-slate-400'
  const errorCellClassName = 'px-3 md:px-6 py-4 text-center text-sm text-rose-300'
  const toggleClassName = 'text-sm font-semibold text-sky-300 hover:text-sky-200'

  return (
    <section className={sectionClassName}>
      <header className={headerClassName}>
        <div className="flex flex-col gap-1">
          <h2 className={titleClassName}>Agents &amp; API leaderboard</h2>
          <p className={subtitleClassName}>Ranked by task volume for the selected period.</p>
        </div>
      </header>

      <div className="overflow-x-auto">
        <table className={tableClassName}>
          <thead className={tableHeadClassName}>
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
          <tbody className={tableBodyClassName}>
            {!queryInput ? (
              <tr>
                <td className={emptyCellClassName} colSpan={columns.length}>
                  Select a date range to view agent and API performance.
                </td>
              </tr>
            ) : isPending ? (
              <tr>
                <td className={emptyCellClassName} colSpan={columns.length}>
                  Loading agent and API activity…
                </td>
              </tr>
            ) : isError ? (
              <tr>
                <td className={errorCellClassName} colSpan={columns.length}>
                  {error?.message || 'Unable to load agent and API leaderboard right now.'}
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td className={emptyCellClassName} colSpan={columns.length}>
                  No agent or API activity yet.
                </td>
              </tr>
            ) : (
              visibleRows.map((row) => (
                <tr key={row.id}>
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
      {hasOverflowRows ? (
        <div className="border-t border-slate-200/10 px-6 py-4">
          <button type="button" className={toggleClassName} onClick={() => setShowAllRows((current) => !current)}>
            {showAllRows ? 'Show less' : `Show more (${sortedRows.length - DEFAULT_VISIBLE_ROWS})`}
          </button>
        </div>
      ) : null}
    </section>
  )
}
