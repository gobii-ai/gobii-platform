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

  const columns = useMemo<ColumnDef<AgentFsNode>[]>(() => {
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
            aria-label="Select all files"
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={row.getIsSelected()}
            disabled={!row.getCanSelect()}
            onChange={row.getToggleSelectedHandler()}
            className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
            aria-label={`Select ${row.original.name}`}
          />
        ),
        size: 48,
      },
      {
        id: 'name',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Name</span>,
        cell: ({ row }) => {
          const isDir = row.original.nodeType === 'dir'
          return (
            <div className="flex items-center gap-3">
              <span className={`flex h-9 w-9 items-center justify-center rounded-lg ${isDir ? 'bg-blue-100 text-blue-700' : 'bg-emerald-100 text-emerald-700'}`}>
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
                <span className="text-sm font-medium text-slate-900">{row.original.name}</span>
                <span className="text-xs text-slate-500">{row.original.path}</span>
              </div>
              {isDir ? <ChevronRight className="h-4 w-4 text-slate-400" aria-hidden="true" /> : null}
            </div>
          )
        },
      },
      {
        id: 'type',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Type</span>,
        cell: ({ row }) => (
          <span className="text-sm text-slate-600">{row.original.nodeType === 'dir' ? 'Folder' : 'File'}</span>
        ),
      },
      {
        id: 'size',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Size</span>,
        cell: ({ row }) => (
          <span className="text-sm text-slate-600">
            {row.original.nodeType === 'dir' ? '-' : formatBytes(row.original.sizeBytes)}
          </span>
        ),
      },
      {
        id: 'updated',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Updated</span>,
        cell: ({ row }) => <span className="text-sm text-slate-600">{formatTimestamp(row.original.updatedAt)}</span>,
      },
      {
        id: 'actions',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Actions</span>,
        cell: ({ row }) => {
          const node = row.original
          if (node.nodeType === 'dir') {
            return (
              <label
                htmlFor={uploadInputId}
                role="button"
                tabIndex={isBusy ? -1 : 0}
                aria-disabled={isBusy}
                className={`inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 transition ${isBusy ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-blue-100'}`}
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
                className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
              >
                <ArrowDownToLine className="h-3.5 w-3.5" />
                Download
              </a>
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-100"
                onClick={() => onDeleteNode(node)}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete
              </button>
            </div>
          )
        },
      },
    ]
  }, [downloadBaseUrl, handleFolderKeyDown, handleUploadKeyDown, isBusy, onDeleteNode, onOpenFolder, onRequestUpload, uploadInputId])

  const table = useReactTable({
    data: rows,
    columns,
    state: { rowSelection },
    onRowSelectionChange,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    enableRowSelection: (row) => row.original.nodeType === 'file',
  })

  return (
    <div className="overflow-x-auto" onDragOver={dragAndDrop.onCurrentFolderDragOver} onDrop={dragAndDrop.onCurrentFolderDrop}>
      <table className="w-full border-collapse">
        <thead className="bg-blue-50/70">
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
              <td colSpan={columns.length} className="px-4 py-6 text-center text-sm text-slate-500">
                Loading files...
              </td>
            </tr>
          ) : errorMessage ? (
            <tr>
              <td colSpan={columns.length} className="px-4 py-6 text-center text-sm text-rose-600">
                {errorMessage}
              </td>
            </tr>
          ) : (
            <>
              {currentFolderId ? (
                <tr
                  className={[
                    'cursor-pointer bg-blue-50/40',
                    dragAndDrop.dragOverNodeId === dragAndDrop.parentDropKey ? 'bg-blue-100/70' : '',
                  ].join(' ')}
                  onClick={onNavigateToParent}
                  onDragOver={dragAndDrop.onParentDragOver}
                  onDragEnter={dragAndDrop.onParentDragEnter}
                  onDragLeave={dragAndDrop.onParentDragLeave}
                  onDrop={dragAndDrop.onParentDrop}
                >
                  <td className="px-4 py-3 align-middle">
                    <input
                      type="checkbox"
                      disabled
                      className="h-4 w-4 rounded border-slate-300 text-blue-600 opacity-50"
                      aria-label="Parent folder selection disabled"
                    />
                  </td>
                  <td colSpan={columns.length - 1} className="px-4 py-3">
                    <div className="flex items-center gap-3 text-sm text-slate-700">
                      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-100 text-blue-700">
                        <ArrowUp className="h-4 w-4" aria-hidden="true" />
                      </span>
                      <div className="flex flex-col">
                        <span className="text-sm font-semibold text-slate-900">Parent folder</span>
                        <span className="text-xs text-slate-500">{parentFolderPath}</span>
                      </div>
                    </div>
                  </td>
                </tr>
              ) : null}
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className="px-4 py-6 text-center text-sm text-slate-500">
                    This folder is empty. Upload files or create a folder to get started.
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={[
                      row.getIsSelected() ? 'bg-blue-50/50' : '',
                      dragAndDrop.dragOverNodeId === row.original.id ? 'bg-blue-100/70' : '',
                    ].join(' ')}
                    draggable={!isBusy}
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
