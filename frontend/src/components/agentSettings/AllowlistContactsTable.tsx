import { useEffect, useMemo, useState } from 'react'
import type { ColumnDef, RowSelectionState } from '@tanstack/react-table'
import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import { ArrowDownToLine, ArrowUpFromLine, Mail, Phone, Trash2 } from 'lucide-react'

import type { AllowlistTableRow } from './contactTypes'

type AllowlistContactsTableProps = {
  rows: AllowlistTableRow[]
  disabled?: boolean
  embedded?: boolean
  onRemoveRow: (row: AllowlistTableRow) => void
  onRemoveRows: (rows: AllowlistTableRow[]) => void
}

function isRowSelectable(row: AllowlistTableRow) {
  return row.pendingType !== 'remove' && row.pendingType !== 'cancel_invite'
}

function renderStatus(row: AllowlistTableRow) {
  if (row.pendingType === 'create') {
    return <span className="inline-flex rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">Pending create</span>
  }
  if (row.pendingType === 'remove') {
    return <span className="inline-flex rounded-full bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700">Pending removal</span>
  }
  if (row.pendingType === 'cancel_invite') {
    return <span className="inline-flex rounded-full bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700">Pending cancel</span>
  }
  if (row.kind === 'invite') {
    return <span className="inline-flex rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">Invite pending</span>
  }
  return <span className="inline-flex rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700">Allowed</span>
}

export function AllowlistContactsTable({ rows, disabled = false, embedded = false, onRemoveRow, onRemoveRows }: AllowlistContactsTableProps) {
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})

  useEffect(() => {
    const selectableIds = new Set(rows.filter(isRowSelectable).map((row) => row.id))
    setRowSelection((prev) => {
      let changed = false
      const next: RowSelectionState = {}
      for (const [key, value] of Object.entries(prev)) {
        if (!value) {
          continue
        }
        if (selectableIds.has(key)) {
          next[key] = true
          continue
        }
        changed = true
      }
      return changed ? next : prev
    })
  }, [rows])

  const columns = useMemo<ColumnDef<AllowlistTableRow>[]>(() => {
    return [
      {
        id: 'select',
        header: ({ table }) => (
          <input
            type="checkbox"
            checked={table.getIsAllRowsSelected()}
            ref={(input) => {
              if (input) {
                input.indeterminate = table.getIsSomeRowsSelected()
              }
            }}
            onChange={table.getToggleAllRowsSelectedHandler()}
            className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
            aria-label="Select all contacts"
            disabled={disabled}
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={row.getIsSelected()}
            disabled={disabled || !row.getCanSelect()}
            onChange={row.getToggleSelectedHandler()}
            className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
            aria-label={`Select ${row.original.address}`}
          />
        ),
        size: 48,
      },
      {
        id: 'contact',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Contact</span>,
        cell: ({ row }) => {
          const Icon = row.original.channel.toLowerCase() === 'sms' ? Phone : Mail
          return (
            <div className="flex items-start gap-3">
              <span className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-700">
                <Icon className="h-4 w-4" aria-hidden="true" />
              </span>
              <div>
                <div className="text-sm font-medium text-slate-900">{row.original.address}</div>
                <div className="text-xs text-slate-500">
                  {row.original.kind === 'invite' ? 'Pending invitation' : 'Allowed contact'}
                </div>
              </div>
            </div>
          )
        },
      },
      {
        id: 'permissions',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Permissions</span>,
        cell: ({ row }) => (
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-xs text-slate-600">
              <ArrowDownToLine className={`h-3.5 w-3.5 ${row.original.allowInbound ? 'text-emerald-600' : 'text-slate-300'}`} aria-hidden="true" />
              <span className={row.original.allowInbound ? 'text-emerald-700' : 'text-slate-400 line-through'}>Receives from contact</span>
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-600">
              <ArrowUpFromLine className={`h-3.5 w-3.5 ${row.original.allowOutbound ? 'text-sky-600' : 'text-slate-300'}`} aria-hidden="true" />
              <span className={row.original.allowOutbound ? 'text-sky-700' : 'text-slate-400 line-through'}>Sends to contact</span>
            </div>
          </div>
        ),
      },
      {
        id: 'status',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Status</span>,
        cell: ({ row }) => renderStatus(row.original),
      },
      {
        id: 'actions',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Actions</span>,
        cell: ({ row }) => {
          const actionIsPending = row.original.pendingType === 'remove' || row.original.pendingType === 'cancel_invite'
          if (actionIsPending) {
            return (
              <span className="text-xs font-medium text-slate-500">
                {row.original.pendingType === 'remove' ? 'Remove on save' : 'Cancel on save'}
              </span>
            )
          }

          return (
            <button
              type="button"
              onClick={() => onRemoveRow(row.original)}
              disabled={disabled}
              className={embedded ? 'inline-flex items-center gap-2 rounded-lg border border-rose-300/70 bg-transparent px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-50/70 disabled:opacity-50' : 'inline-flex items-center gap-2 rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:opacity-50'}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              {row.original.kind === 'invite' ? 'Cancel invite' : 'Remove'}
            </button>
          )
        },
      },
    ]
  }, [disabled, onRemoveRow])

  const table = useReactTable({
    data: rows,
    columns,
    state: { rowSelection },
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    enableRowSelection: (row) => isRowSelectable(row.original),
  })

  const selectedRows = table.getSelectedRowModel().rows.map((row) => row.original)

  return (
    <div className="space-y-4">
      {selectedRows.length > 0 && (
        <div className="flex flex-col gap-3 rounded-xl border border-blue-200 bg-blue-50/70 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm text-slate-700">
            {selectedRows.length} contact{selectedRows.length === 1 ? '' : 's'} selected
          </div>
          <button
            type="button"
            onClick={() => onRemoveRows(selectedRows)}
            disabled={disabled}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            Remove selected
          </button>
        </div>
      )}

      <div className={embedded ? 'overflow-hidden rounded-xl border border-slate-200/70 bg-transparent' : 'overflow-hidden rounded-xl border border-slate-200'}>
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse">
            <thead className={embedded ? 'bg-transparent' : 'bg-white'}>
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id} className="border-b border-slate-200">
                  {headerGroup.headers.map((header) => (
                    <th key={header.id} scope="col" className="px-4 py-3 text-left align-middle">
                      {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody className={embedded ? 'bg-transparent' : 'bg-white'}>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className="px-4 py-10 text-center text-sm text-slate-500">
                    No additional contacts configured yet.
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={[
                      'border-b border-slate-100 last:border-b-0',
                      row.getIsSelected() ? 'bg-blue-50/50' : '',
                      row.original.pendingType === 'remove' || row.original.pendingType === 'cancel_invite' ? 'opacity-60' : '',
                    ].join(' ')}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-4 py-4 align-middle">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
