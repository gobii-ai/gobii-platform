import { useMemo } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'
import { Clock3, Trash2, UserPlus, Users } from 'lucide-react'

import type { CollaboratorTableRow } from './contactTypes'

type CollaboratorsTableProps = {
  rows: CollaboratorTableRow[]
  disabled?: boolean
  canManage: boolean
  onRemove: (row: CollaboratorTableRow) => void
}

function renderStatus(row: CollaboratorTableRow) {
  if (row.pendingType === 'create') {
    return <span className="inline-flex rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">Pending create</span>
  }
  if (row.pendingType === 'remove') {
    return <span className="inline-flex rounded-full bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700">Pending removal</span>
  }
  if (row.pendingType === 'cancel_invite') {
    return <span className="inline-flex rounded-full bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700">Pending cancel</span>
  }
  if (row.kind === 'active') {
    return <span className="inline-flex rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700">Active</span>
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">
      <Clock3 className="h-3 w-3" aria-hidden="true" />
      Pending invite
    </span>
  )
}

export function CollaboratorsTable({
  rows,
  disabled = false,
  canManage,
  onRemove,
}: CollaboratorsTableProps) {
  const columns = useMemo<ColumnDef<CollaboratorTableRow>[]>(
    () => [
      {
        id: 'collaborator',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Collaborator</span>,
        cell: ({ row }) => {
          const icon =
            row.original.kind === 'active'
              ? <Users className="h-4 w-4" aria-hidden="true" />
              : <UserPlus className="h-4 w-4" aria-hidden="true" />
          return (
            <div className="flex items-start gap-3">
              <span className={`mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg ${row.original.kind === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                {icon}
              </span>
              <div>
                <div className="text-sm font-medium text-slate-900">{row.original.name || row.original.email}</div>
                <div className="text-xs text-slate-500">{row.original.email}</div>
              </div>
            </div>
          )
        },
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
          if (!canManage) {
            return <span className="text-xs text-slate-500">Managed by owner/admin</span>
          }

          if (row.original.pendingType === 'remove' || row.original.pendingType === 'cancel_invite') {
            return (
              <span className="text-xs font-medium text-slate-500">
                {row.original.pendingType === 'remove' ? 'Remove on save' : 'Cancel on save'}
              </span>
            )
          }

          return (
            <button
              type="button"
              onClick={() => onRemove(row.original)}
              disabled={disabled}
              className="inline-flex items-center gap-2 rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              {row.original.kind === 'active' ? 'Remove' : 'Cancel invite'}
            </button>
          )
        },
      },
    ],
    [canManage, disabled, onRemove],
  )

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
  })

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200">
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse">
          <thead className="bg-white">
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
          <tbody className="bg-white">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-4 py-10 text-center text-sm text-slate-500">
                  No collaborators yet.
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  className={[
                    'border-b border-slate-100 last:border-b-0',
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
  )
}
