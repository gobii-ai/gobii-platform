import { useCallback, useMemo } from 'react'
import type { KeyboardEvent, MouseEvent } from 'react'

import {
  type ColumnDef,
  type OnChangeFn,
  type RowSelectionState,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { ArrowDownToLine, ArrowUp, ChevronRight, FileText, Folder, Trash2, UploadCloud } from 'lucide-react'

import type { FileDragAndDropHandlers } from './useFileDragAndDrop'
import type { AgentFsNode } from './types'
import { formatBytes, formatTimestamp } from './utils'

type FileTableProps = {
  rows: AgentFsNode[]
  isBusy: boolean
  isLoading: boolean
  errorMessage: string | null
  embedded?: boolean
  canManage: boolean
  currentFolderId: string | null
  parentFolderPath: string
  rowSelection: RowSelectionState
  onRowSelectionChange: OnChangeFn<RowSelectionState>
  onNavigateToParent: () => void
  onOpenFolder: (node: AgentFsNode) => void
  onRequestUpload: (parentId: string | null) => void
  onTriggerUploadInput: () => void
  onDeleteNode: (node: AgentFsNode) => void
  downloadBaseUrl: string
  uploadInputId: string
  dragAndDrop: FileDragAndDropHandlers
}

export function FileTable({
  rows,
  isBusy,
  isLoading,
  errorMessage,
  embedded = false,
  canManage,
  currentFolderId,
  parentFolderPath,
  rowSelection,
  onRowSelectionChange,
  onNavigateToParent,
  onOpenFolder,
  onRequestUpload,
  onTriggerUploadInput,
  onDeleteNode,
  downloadBaseUrl,
  uploadInputId,
  dragAndDrop,
}: FileTableProps) {
  const handleFolderKeyDown = useCallback(
    (node: AgentFsNode, event: KeyboardEvent<HTMLDivElement>) => {
      if (node.nodeType !== 'dir') {
        return
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        onOpenFolder(node)
      }
    },
    [onOpenFolder],
  )

  const handleRowDoubleClick = useCallback(
    (node: AgentFsNode, event: MouseEvent<HTMLTableRowElement>) => {
      if (node.nodeType !== 'dir') {
        return
      }
      const target = event.target as HTMLElement | null
      if (target?.closest('button, a, input')) {
        return
      }
      onOpenFolder(node)
    },
    [onOpenFolder],
  )

  const handleUploadKeyDown = useCallback(
    (parentId: string | null, event: KeyboardEvent<HTMLLabelElement>) => {
      if (isBusy) {
        return
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        onRequestUpload(parentId)
        onTriggerUploadInput()
      }
    },
    [isBusy, onRequestUpload, onTriggerUploadInput],
  )

  const showSelection = canManage
  const selectionInputClassName = embedded
    ? 'h-4 w-4 rounded border-slate-500 bg-slate-900/40 text-blue-400 focus:ring-blue-400'
    : 'h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500'
  const tableTextMutedClassName = embedded ? 'text-slate-300' : 'text-slate-500'
  const folderBadgeClassName = embedded
    ? 'border border-blue-300/20 bg-blue-950/30 text-blue-200'
    : 'bg-blue-100 text-blue-700'
  const fileBadgeClassName = embedded
    ? 'border border-emerald-300/20 bg-emerald-950/30 text-emerald-200'
    : 'bg-emerald-100 text-emerald-700'
  const folderUploadButtonClassName = embedded
    ? `inline-flex items-center gap-2 rounded-lg border border-blue-300/40 bg-blue-950/20 px-3 py-1.5 text-xs font-semibold text-blue-100 transition ${isBusy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:border-blue-200 hover:bg-blue-900/30'}`
    : `inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 transition ${isBusy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-blue-100'}`
  const downloadButtonClassName = embedded
    ? 'inline-flex items-center gap-2 rounded-lg border border-blue-300/40 bg-blue-950/20 px-3 py-1.5 text-xs font-semibold text-blue-100 transition hover:border-blue-200 hover:bg-blue-900/30'
    : 'inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 transition hover:bg-blue-100'
  const deleteButtonClassName = embedded
    ? 'inline-flex items-center gap-2 rounded-lg border border-rose-300/40 bg-rose-950/20 px-3 py-1.5 text-xs font-semibold text-rose-100 transition hover:border-rose-200 hover:bg-rose-900/30'
    : 'inline-flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100'
  const columns = useMemo<ColumnDef<AgentFsNode>[]>(() => {
    const baseColumns: ColumnDef<AgentFsNode>[] = []
    if (showSelection) {
      baseColumns.push({
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
            className={selectionInputClassName}
            aria-label="Select all files"
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={row.getIsSelected()}
            disabled={!row.getCanSelect()}
            onChange={row.getToggleSelectedHandler()}
            className={`${selectionInputClassName} disabled:opacity-50`}
            aria-label={`Select ${row.original.name}`}
          />
        ),
        size: 48,
      })
    }

    baseColumns.push({
        id: 'name',
        header: () => <span className={`text-xs font-semibold uppercase tracking-wide ${tableTextMutedClassName}`}>Name</span>,
        cell: ({ row }) => {
          const isDir = row.original.nodeType === 'dir'
          return (
            <div className="flex items-center gap-3">
              <span className={`flex h-9 w-9 items-center justify-center rounded-lg ${isDir ? folderBadgeClassName : fileBadgeClassName}`}>
                {isDir ? <Folder className="h-4 w-4" /> : <FileText className="h-4 w-4" />}
              </span>
              <div
                className={`flex flex-1 flex-col ${isDir ? 'cursor-pointer' : ''}`}
                onClick={isDir ? () => onOpenFolder(row.original) : undefined}
                onKeyDown={(event) => handleFolderKeyDown(row.original, event)}
                role={isDir ? 'button' : undefined}
                tabIndex={isDir ? 0 : undefined}
                title={isDir ? 'Open folder' : row.original.name}
              >
                <span className={embedded ? 'text-sm font-medium text-slate-100' : 'text-sm font-medium text-slate-900'}>{row.original.name}</span>
                <span className={embedded ? 'text-xs text-slate-400' : 'text-xs text-slate-500'}>{row.original.path}</span>
              </div>
              {isDir ? <ChevronRight className={embedded ? 'h-4 w-4 text-slate-500' : 'h-4 w-4 text-slate-400'} aria-hidden="true" /> : null}
            </div>
          )
        },
      })
    baseColumns.push({
        id: 'type',
        header: () => <span className={`text-xs font-semibold uppercase tracking-wide ${tableTextMutedClassName}`}>Type</span>,
        cell: ({ row }) => (
          <span className={embedded ? 'text-sm text-slate-300' : 'text-sm text-slate-600'}>{row.original.nodeType === 'dir' ? 'Folder' : 'File'}</span>
        ),
      })
    baseColumns.push({
        id: 'size',
        header: () => <span className={`text-xs font-semibold uppercase tracking-wide ${tableTextMutedClassName}`}>Size</span>,
        cell: ({ row }) => (
          <span className={embedded ? 'text-sm text-slate-300' : 'text-sm text-slate-600'}>
            {row.original.nodeType === 'dir' ? '-' : formatBytes(row.original.sizeBytes)}
          </span>
        ),
      })
    baseColumns.push({
        id: 'updated',
        header: () => <span className={`text-xs font-semibold uppercase tracking-wide ${tableTextMutedClassName}`}>Updated</span>,
        cell: ({ row }) => <span className={embedded ? 'text-sm text-slate-300' : 'text-sm text-slate-600'}>{formatTimestamp(row.original.updatedAt)}</span>,
      })
    baseColumns.push({
        id: 'actions',
        header: () => <span className={`text-xs font-semibold uppercase tracking-wide ${tableTextMutedClassName}`}>Actions</span>,
        cell: ({ row }) => {
          const node = row.original
          if (node.nodeType === 'dir') {
            return (
              <label
                htmlFor={uploadInputId}
                role="button"
                tabIndex={isBusy ? -1 : 0}
                aria-disabled={isBusy}
                className={folderUploadButtonClassName}
                onPointerDown={(event) => {
                  if (isBusy) {
                    event.preventDefault()
                    return
                  }
                  onRequestUpload(node.id)
                }}
                onKeyDown={(event) => handleUploadKeyDown(node.id, event)}
              >
                <UploadCloud className="h-3.5 w-3.5" />
                Upload here
              </label>
            )
          }

          const downloadUrl = `${downloadBaseUrl}?node_id=${encodeURIComponent(node.id)}`
          return (
            <div className="flex flex-wrap items-center gap-2">
              <a
                href={downloadUrl}
                className={downloadButtonClassName}
              >
                <ArrowDownToLine className="h-3.5 w-3.5" />
                Download
              </a>
              {canManage && (
                <button
                  type="button"
                  className={deleteButtonClassName}
                  onClick={() => onDeleteNode(node)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete
                </button>
              )}
            </div>
          )
        },
      })

    return baseColumns
  }, [canManage, deleteButtonClassName, downloadBaseUrl, downloadButtonClassName, embedded, fileBadgeClassName, folderBadgeClassName, folderUploadButtonClassName, handleFolderKeyDown, handleUploadKeyDown, isBusy, onDeleteNode, onOpenFolder, onRequestUpload, selectionInputClassName, showSelection, tableTextMutedClassName, uploadInputId])

  const table = useReactTable({
    data: rows,
    columns,
    state: { rowSelection },
    onRowSelectionChange,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    enableRowSelection: canManage ? (row) => row.original.nodeType === 'file' : false,
  })

  return (
    <div className="overflow-x-auto" onDragOver={dragAndDrop.onCurrentFolderDragOver} onDrop={dragAndDrop.onCurrentFolderDrop}>
      <table className="w-full border-collapse">
        <thead className={embedded ? 'bg-slate-900/40' : 'bg-blue-50/70'}>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th key={header.id} scope="col" className="px-4 py-3 text-left">
                  {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {isLoading ? (
            <tr>
              <td colSpan={columns.length} className={embedded ? 'px-4 py-6 text-center text-sm text-slate-400' : 'px-4 py-6 text-center text-sm text-slate-500'}>
                Loading files...
              </td>
            </tr>
          ) : errorMessage ? (
            <tr>
              <td colSpan={columns.length} className={embedded ? 'px-4 py-6 text-center text-sm text-rose-300' : 'px-4 py-6 text-center text-sm text-rose-600'}>
                {errorMessage}
              </td>
            </tr>
          ) : (
            <>
              {currentFolderId ? (
                <tr
                  className={[
                    'cursor-pointer',
                    embedded ? 'border-b border-slate-200/70 bg-slate-900/30' : 'bg-blue-50/40',
                    dragAndDrop.dragOverNodeId === dragAndDrop.parentDropKey ? (embedded ? 'bg-blue-950/30' : 'bg-blue-100/70') : '',
                  ].join(' ')}
                  onClick={onNavigateToParent}
                  onDragOver={dragAndDrop.onParentDragOver}
                  onDragEnter={dragAndDrop.onParentDragEnter}
                  onDragLeave={dragAndDrop.onParentDragLeave}
                  onDrop={dragAndDrop.onParentDrop}
                >
                  {showSelection && (
                    <td className="px-4 py-3 align-middle">
                      <input
                        type="checkbox"
                        disabled
                        className={`${selectionInputClassName} opacity-50`}
                        aria-label="Parent folder selection disabled"
                      />
                    </td>
                  )}
                  <td colSpan={columns.length - (showSelection ? 1 : 0)} className="px-4 py-3">
                    <div className={embedded ? 'flex items-center gap-3 text-sm text-slate-300' : 'flex items-center gap-3 text-sm text-slate-700'}>
                      <span className={embedded ? 'flex h-8 w-8 items-center justify-center rounded-lg border border-blue-300/20 bg-blue-950/30 text-blue-200' : 'flex h-8 w-8 items-center justify-center rounded-lg bg-blue-100 text-blue-700'}>
                        <ArrowUp className="h-4 w-4" aria-hidden="true" />
                      </span>
                      <div className="flex flex-col">
                        <span className={embedded ? 'text-sm font-semibold text-slate-100' : 'text-sm font-semibold text-slate-900'}>Parent folder</span>
                        <span className={embedded ? 'text-xs text-slate-400' : 'text-xs text-slate-500'}>{parentFolderPath}</span>
                      </div>
                    </div>
                  </td>
                </tr>
              ) : null}
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className={embedded ? 'px-4 py-6 text-center text-sm text-slate-400' : 'px-4 py-6 text-center text-sm text-slate-500'}>
                    This folder is empty. Upload files or create a folder to get started.
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={[
                      row.getIsSelected() ? (embedded ? 'bg-blue-950/20' : 'bg-blue-50/50') : '',
                      dragAndDrop.dragOverNodeId === row.original.id ? (embedded ? 'bg-blue-950/30' : 'bg-blue-100/70') : '',
                      embedded ? 'border-b border-slate-200/70' : '',
                    ].join(' ')}
                    draggable={canManage && !isBusy}
                    onDoubleClick={(event) => handleRowDoubleClick(row.original, event)}
                    onDragStart={(event) => dragAndDrop.onRowDragStart(row.original, event)}
                    onDragEnd={dragAndDrop.onRowDragEnd}
                    onDragOver={(event) => dragAndDrop.onFolderDragOver(row.original, event)}
                    onDragEnter={(event) => dragAndDrop.onFolderDragEnter(row.original, event)}
                    onDragLeave={(event) => dragAndDrop.onFolderDragLeave(row.original, event)}
                    onDrop={(event) => dragAndDrop.onFolderDrop(row.original, event)}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-4 py-4 align-middle">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </>
          )}
        </tbody>
      </table>
    </div>
  )
}
