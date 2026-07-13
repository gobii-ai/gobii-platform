import { useMemo } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { getCoreRowModel, useReactTable } from '@tanstack/react-table'
import { Clock3, UserPlus, Users } from 'lucide-react'

import type { CollaboratorTableRow } from './contactTypes'
import { TanStackTableShell } from '../common/TanStackTableShell'
import { EmbeddedRemoveButton, EmbeddedStatusBadge, EmbeddedTableFrame, EmbeddedTableHeader } from './embeddedTablePrimitives'

type CollaboratorsTableProps = {
  rows: CollaboratorTableRow[]
  disabled?: boolean
  canManage: boolean
  onRemove: (row: CollaboratorTableRow) => void
}

function renderStatus(row: CollaboratorTableRow) {
  if (row.pendingType === 'create') {
    return <EmbeddedStatusBadge tone="pending">Pending create</EmbeddedStatusBadge>
  }
  if (row.pendingType === 'remove') {
    return <EmbeddedStatusBadge tone="danger">Pending removal</EmbeddedStatusBadge>
  }
  if (row.pendingType === 'cancel_invite') {
    return <EmbeddedStatusBadge tone="danger">Pending cancel</EmbeddedStatusBadge>
  }
  if (row.kind === 'active') {
    return <EmbeddedStatusBadge tone="active">Active</EmbeddedStatusBadge>
  }
  return (
    <EmbeddedStatusBadge tone="pending">
      <Clock3 className="h-3 w-3" aria-hidden="true" />
      Pending invite
    </EmbeddedStatusBadge>
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
        header: () => <EmbeddedTableHeader>Collaborator</EmbeddedTableHeader>,
        cell: ({ row }) => {
          const icon =
            row.original.kind === 'active'
              ? <Users className="h-4 w-4" aria-hidden="true" />
              : <UserPlus className="h-4 w-4" aria-hidden="true" />
          return (
            <div className="flex items-start gap-3">
              <span
                className={`mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg ${
                  row.original.kind === 'active'
                    ? 'border border-emerald-300/15 bg-emerald-950/40 text-emerald-200'
                    : 'border border-amber-300/15 bg-amber-950/35 text-amber-200'
                }`}
              >
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
        header: () => <EmbeddedTableHeader>Status</EmbeddedTableHeader>,
        cell: ({ row }) => renderStatus(row.original),
      },
      {
        id: 'actions',
        header: () => <EmbeddedTableHeader>Actions</EmbeddedTableHeader>,
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
            <EmbeddedRemoveButton onClick={() => onRemove(row.original)} disabled={disabled}>
              {row.original.kind === 'active' ? 'Remove' : 'Cancel invite'}
            </EmbeddedRemoveButton>
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
    <EmbeddedTableFrame>
      <TanStackTableShell
        table={table}
        emptyState={{ content: 'No collaborators yet.' }}
        getRowProps={(row) => ({
          className: row.original.pendingType === 'remove' || row.original.pendingType === 'cancel_invite'
            ? 'opacity-60'
            : '',
        })}
      />
    </EmbeddedTableFrame>
  )
}
