import { useEffect, useMemo, useState } from 'react'
import type { ColumnDef, RowSelectionState } from '@tanstack/react-table'
import { getCoreRowModel, useReactTable } from '@tanstack/react-table'
import { ArrowDownToLine, ArrowUpFromLine, Mail, Pencil, Phone, Trash2 } from 'lucide-react'

import type { AllowlistTableRow } from './contactTypes'
import { TanStackTableShell } from '../common/TanStackTableShell'
import {
  EmbeddedRemoveButton,
  EmbeddedStatusBadge,
  EmbeddedTableActionButton,
  EmbeddedTableFrame,
  EmbeddedTableHeader,
  embeddedBulkBannerClassName,
  embeddedBulkButtonClassName,
} from './embeddedTablePrimitives'

type AllowlistContactsTableProps = {
  rows: AllowlistTableRow[]
  disabled?: boolean
  onEditRow: (row: AllowlistTableRow) => void
  onRemoveRow: (row: AllowlistTableRow) => void
  onRemoveRows: (rows: AllowlistTableRow[]) => void
}

function isRowSelectable(row: AllowlistTableRow) {
  return row.pendingType !== 'remove' && row.pendingType !== 'cancel_invite'
}

function renderStatus(row: AllowlistTableRow) {
  if (row.pendingType === 'create') {
    return <EmbeddedStatusBadge tone="pending">Pending create</EmbeddedStatusBadge>
  }
  if (row.pendingType === 'remove') {
    return <EmbeddedStatusBadge tone="danger">Pending removal</EmbeddedStatusBadge>
  }
  if (row.pendingType === 'update') {
    return <EmbeddedStatusBadge tone="pending">Pending changes</EmbeddedStatusBadge>
  }
  if (row.pendingType === 'cancel_invite') {
    return <EmbeddedStatusBadge tone="danger">Pending cancel</EmbeddedStatusBadge>
  }
  if (row.kind === 'invite') {
    return <EmbeddedStatusBadge tone="pending">Invite pending</EmbeddedStatusBadge>
  }
  return <EmbeddedStatusBadge tone="active">Allowed</EmbeddedStatusBadge>
}

export function AllowlistContactsTable({ rows, disabled = false, onEditRow, onRemoveRow, onRemoveRows }: AllowlistContactsTableProps) {
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
            className="h-4 w-4 rounded border-slate-300/40 bg-slate-950/70 text-blue-500 focus:ring-blue-500 focus:ring-offset-0"
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
            className="h-4 w-4 rounded border-slate-300/40 bg-slate-950/70 text-blue-500 focus:ring-blue-500 focus:ring-offset-0 disabled:opacity-50"
            aria-label={`Select ${row.original.address}`}
          />
        ),
        size: 48,
      },
      {
        id: 'contact',
        header: () => <EmbeddedTableHeader>Contact</EmbeddedTableHeader>,
        cell: ({ row }) => {
          const Icon = row.original.channel.toLowerCase() === 'sms' ? Phone : Mail
          return (
            <div className="flex items-start gap-3">
              <span className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg border border-sky-300/15 bg-sky-950/45 text-sky-200">
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
        header: () => <EmbeddedTableHeader>Permissions</EmbeddedTableHeader>,
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
        header: () => <EmbeddedTableHeader>Status</EmbeddedTableHeader>,
        cell: ({ row }) => renderStatus(row.original),
      },
      {
        id: 'actions',
        header: () => <EmbeddedTableHeader>Actions</EmbeddedTableHeader>,
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
            <div className="flex items-center gap-2">
              {row.original.kind === 'entry' && (
                <EmbeddedTableActionButton
                  icon={Pencil}
                  onClick={() => onEditRow(row.original)}
                  disabled={disabled}
                >
                  Edit
                </EmbeddedTableActionButton>
              )}
              <EmbeddedRemoveButton onClick={() => onRemoveRow(row.original)} disabled={disabled}>
                {row.original.kind === 'invite' ? 'Cancel invite' : 'Remove'}
              </EmbeddedRemoveButton>
            </div>
          )
        },
      },
    ]
  }, [disabled, onEditRow, onRemoveRow])

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
        <div className={embeddedBulkBannerClassName}>
          <div className="text-sm text-slate-100">
            {selectedRows.length} contact{selectedRows.length === 1 ? '' : 's'} selected
          </div>
          <button
            type="button"
            onClick={() => onRemoveRows(selectedRows)}
            disabled={disabled}
            className={embeddedBulkButtonClassName}
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            Remove selected
          </button>
        </div>
      )}

      <EmbeddedTableFrame>
        <TanStackTableShell
          table={table}
          emptyState={{ content: 'No additional contacts configured yet.' }}
          getRowProps={(row) => ({
            className: [
              row.getIsSelected() ? 'bg-sky-950/35' : '',
              row.original.pendingType === 'remove' || row.original.pendingType === 'cancel_invite'
                ? 'opacity-60'
                : '',
            ].join(' '),
          })}
        />
      </EmbeddedTableFrame>
    </div>
  )
}
