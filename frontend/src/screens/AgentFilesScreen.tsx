import { useCallback, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  type ColumnDef,
  type ExpandedState,
  type RowSelectionState,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { ArrowDownToLine, ArrowLeft, ChevronRight, FileText, Folder, RefreshCw, Trash2, UploadCloud } from 'lucide-react'

import { HttpError, getCsrfToken, jsonFetch, jsonRequest } from '../api/http'

type AgentFsNode = {
  id: string
  parentId: string | null
  name: string
  path: string
  nodeType: 'dir' | 'file'
  sizeBytes: number | null
  mimeType: string | null
  createdAt: string | null
  updatedAt: string | null
}

type AgentFilesResponse = {
  filespace: {
    id: string
    name: string
  }
  nodes: AgentFsNode[]
}

type AgentFilesPageData = {
  csrfToken: string
  agent: {
    id: string
    name: string
  }
  urls: {
    agentDetail: string
    files: string
    upload: string
    delete: string
    download: string
  }
}

export type AgentFilesScreenProps = {
  initialData: AgentFilesPageData
}

type FilesRow = {
  node: AgentFsNode
  children?: FilesRow[]
}

type UploadPayload = {
  files: FileList
  parentId: string | null
}

type DeletePayload = {
  nodeIds: string[]
}

function formatBytes(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return '-'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let current = value
  let idx = 0
  while (current >= 1024 && idx < units.length - 1) {
    current /= 1024
    idx += 1
  }
  return `${current.toFixed(current >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`
}

function formatTimestamp(iso: string | null): string {
  if (!iso) {
    return '-'
  }
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) {
    return '-'
  }
  return parsed.toLocaleString()
}

function sortNodes(a: FilesRow, b: FilesRow): number {
  if (a.node.nodeType !== b.node.nodeType) {
    return a.node.nodeType === 'dir' ? -1 : 1
  }
  return a.node.name.localeCompare(b.node.name)
}

function buildTree(nodes: AgentFsNode[]): FilesRow[] {
  const map = new Map<string, FilesRow>()
  const roots: FilesRow[] = []

  nodes.forEach((node) => {
    map.set(node.id, { node, children: [] })
  })

  nodes.forEach((node) => {
    const current = map.get(node.id)
    if (!current) {
      return
    }
    const parentId = node.parentId
    if (parentId && map.has(parentId)) {
      const parent = map.get(parentId)
      if (parent) {
        parent.children?.push(current)
      }
    } else {
      roots.push(current)
    }
  })

  const sortTree = (rows: FilesRow[]) => {
    rows.sort(sortNodes)
    rows.forEach((row) => {
      if (row.children && row.children.length > 0) {
        sortTree(row.children)
      }
    })
  }

  sortTree(roots)
  return roots
}

function resolveErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof HttpError) {
    if (typeof error.body === 'string' && error.body) {
      return error.body
    }
    if (typeof error.statusText === 'string' && error.statusText) {
      return error.statusText
    }
  }
  if (error && typeof error === 'object' && 'message' in error && typeof (error as { message: unknown }).message === 'string') {
    return (error as { message: string }).message
  }
  return fallback
}

async function uploadFiles(url: string, payload: UploadPayload): Promise<AgentFsNode[]> {
  const formData = new FormData()
  Array.from(payload.files).forEach((file) => {
    formData.append('files', file)
  })
  if (payload.parentId) {
    formData.append('parent_id', payload.parentId)
  }

  const response = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'X-CSRFToken': getCsrfToken(),
      Accept: 'application/json',
    },
    body: formData,
  })

  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.text()
    } catch (error) {
      body = null
    }
    throw new HttpError(response.status, response.statusText, body)
  }

  const payloadJson = (await response.json()) as { created?: AgentFsNode[] }
  return payloadJson.created ?? []
}

export function AgentFilesScreen({ initialData }: AgentFilesScreenProps) {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState<ExpandedState>({})
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [uploadTarget, setUploadTarget] = useState<AgentFsNode | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const filesQuery = useQuery<AgentFilesResponse, Error>({
    queryKey: ['agent-files', initialData.agent.id],
    queryFn: ({ signal }) => jsonFetch<AgentFilesResponse>(initialData.urls.files, { signal }),
    refetchOnWindowFocus: false,
  })

  const nodes = filesQuery.data?.nodes ?? []
  const filespaceName = filesQuery.data?.filespace?.name ?? 'Agent Files'
  const treeRows = useMemo(() => buildTree(nodes), [nodes])

  const uploadMutation = useMutation({
    mutationFn: (payload: UploadPayload) => uploadFiles(initialData.urls.upload, payload),
    onSuccess: async () => {
      setActionError(null)
      await queryClient.invalidateQueries({ queryKey: ['agent-files', initialData.agent.id] })
    },
    onError: (error) => {
      setActionError(resolveErrorMessage(error, 'Failed to upload files.'))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (payload: DeletePayload) =>
      jsonRequest<{ deleted: number }>(initialData.urls.delete, {
        method: 'POST',
        json: { nodeIds: payload.nodeIds },
        includeCsrf: true,
      }),
    onSuccess: async () => {
      setActionError(null)
      setRowSelection({})
      await queryClient.invalidateQueries({ queryKey: ['agent-files', initialData.agent.id] })
    },
    onError: (error) => {
      setActionError(resolveErrorMessage(error, 'Failed to delete files.'))
    },
  })

  const handleUploadClick = useCallback((target: AgentFsNode | null) => {
    setUploadTarget(target)
    fileInputRef.current?.click()
  }, [])

  const handleFileChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files
      if (!files || files.length === 0) {
        return
      }
      try {
        await uploadMutation.mutateAsync({
          files,
          parentId: uploadTarget?.id ?? null,
        })
      } catch (error) {
        // Errors are surfaced via mutation callbacks.
      }
      event.target.value = ''
    },
    [uploadMutation, uploadTarget],
  )

  const selectedNodes = useMemo(() => {
    return Object.keys(rowSelection).filter((key) => rowSelection[key])
  }, [rowSelection])
  const selectedRows = selectedNodes.length

  const handleBulkDelete = useCallback(async () => {
    if (!selectedNodes.length) {
      return
    }
    const confirmed = window.confirm(`Delete ${selectedNodes.length} file${selectedNodes.length === 1 ? '' : 's'}?`)
    if (!confirmed) {
      return
    }
    try {
      await deleteMutation.mutateAsync({ nodeIds: selectedNodes })
    } catch (error) {
      // Errors are surfaced via mutation callbacks.
    }
  }, [deleteMutation, selectedNodes])

  const handleSingleDelete = useCallback(async (node: AgentFsNode) => {
    const confirmed = window.confirm(`Delete ${node.name}?`)
    if (!confirmed) {
      return
    }
    try {
      await deleteMutation.mutateAsync({ nodeIds: [node.id] })
    } catch (error) {
      // Errors are surfaced via mutation callbacks.
    }
  }, [deleteMutation])

  const columns = useMemo<ColumnDef<FilesRow>[]>(() => {
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
            aria-label={`Select ${row.original.node.name}`}
          />
        ),
        size: 48,
      },
      {
        id: 'name',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Name</span>,
        cell: ({ row }) => {
          const isDir = row.original.node.nodeType === 'dir'
          const indent = row.depth * 16
          return (
            <div className="flex items-center gap-2" style={{ paddingLeft: `${indent}px` }}>
              {row.getCanExpand() ? (
                <button
                  type="button"
                  onClick={row.getToggleExpandedHandler()}
                  className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 hover:bg-blue-50"
                  aria-label={row.getIsExpanded() ? 'Collapse folder' : 'Expand folder'}
                >
                  <ChevronRight className={`h-4 w-4 transition-transform ${row.getIsExpanded() ? 'rotate-90' : ''}`} />
                </button>
              ) : (
                <span className="h-6 w-6" aria-hidden="true" />
              )}
              <span className={`flex h-9 w-9 items-center justify-center rounded-lg ${isDir ? 'bg-blue-100 text-blue-700' : 'bg-emerald-100 text-emerald-700'}`}>
                {isDir ? <Folder className="h-4 w-4" /> : <FileText className="h-4 w-4" />}
              </span>
              <div className="flex flex-col">
                <span className="text-sm font-medium text-slate-900">{row.original.node.name}</span>
                <span className="text-xs text-slate-500">{row.original.node.path}</span>
              </div>
            </div>
          )
        },
      },
      {
        id: 'type',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Type</span>,
        cell: ({ row }) => (
          <span className="text-sm text-slate-600">{row.original.node.nodeType === 'dir' ? 'Folder' : 'File'}</span>
        ),
      },
      {
        id: 'size',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Size</span>,
        cell: ({ row }) => (
          <span className="text-sm text-slate-600">
            {row.original.node.nodeType === 'dir' ? '-' : formatBytes(row.original.node.sizeBytes)}
          </span>
        ),
      },
      {
        id: 'updated',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Updated</span>,
        cell: ({ row }) => <span className="text-sm text-slate-600">{formatTimestamp(row.original.node.updatedAt)}</span>,
      },
      {
        id: 'actions',
        header: () => <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Actions</span>,
        cell: ({ row }) => {
          const node = row.original.node
          if (node.nodeType === 'dir') {
            return (
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
                onClick={() => handleUploadClick(node)}
              >
                <UploadCloud className="h-3.5 w-3.5" />
                Upload here
              </button>
            )
          }

          const downloadUrl = `${initialData.urls.download}?path=${encodeURIComponent(node.path)}`
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
                onClick={() => handleSingleDelete(node)}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete
              </button>
            </div>
          )
        },
      },
    ]
  }, [handleSingleDelete, handleUploadClick, initialData.urls.download])

  const table = useReactTable({
    data: treeRows,
    columns,
    state: { expanded, rowSelection },
    onExpandedChange: setExpanded,
    onRowSelectionChange: setRowSelection,
    getSubRows: (row) => row.children ?? [],
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getRowId: (row) => row.node.id,
    enableRowSelection: (row) => row.original.node.nodeType === 'file',
  })

  const handleRefresh = useCallback(() => {
    filesQuery.refetch().catch(() => {})
  }, [filesQuery])

  const uploadLabel = uploadTarget ? `Upload target: ${uploadTarget.path}` : 'Upload target: /'
  const isBusy = uploadMutation.isPending || deleteMutation.isPending

  return (
    <div className="space-y-6 pb-6">
      <div className="gobii-card-base overflow-hidden">
        <div className="flex flex-col gap-4 border-b border-slate-200/70 px-6 py-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Agent Files</h1>
            <p className="mt-1 text-sm text-slate-600">Browse and manage files for {initialData.agent.name}.</p>
            <a href={initialData.urls.agentDetail} className="mt-3 inline-flex items-center gap-2 text-sm text-blue-700 hover:text-blue-900">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Back to Agent Settings
            </a>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-semibold text-blue-700 transition hover:bg-blue-100 disabled:opacity-60"
              onClick={() => handleUploadClick(null)}
              disabled={isBusy}
            >
              <UploadCloud className="h-4 w-4" aria-hidden="true" />
              Upload Files
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-700 transition hover:bg-rose-100 disabled:opacity-60"
              onClick={handleBulkDelete}
              disabled={isBusy || selectedRows === 0}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Delete Selected
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-blue-50 disabled:opacity-60"
              onClick={handleRefresh}
              disabled={filesQuery.isFetching}
            >
              <RefreshCw className={`h-4 w-4 ${filesQuery.isFetching ? 'animate-spin' : ''}`} aria-hidden="true" />
              Refresh
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-3 px-6 py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-col">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Filespace</span>
              <span className="text-sm font-medium text-slate-800">{filespaceName}</span>
            </div>
            <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">{uploadLabel}</span>
          </div>
          {actionError && <p className="text-sm text-rose-600">{actionError}</p>}
        </div>

        <div className="overflow-x-auto">
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
              {filesQuery.isPending ? (
                <tr>
                  <td colSpan={columns.length} className="px-4 py-6 text-center text-sm text-slate-500">
                    Loading files...
                  </td>
                </tr>
              ) : filesQuery.isError ? (
                <tr>
                  <td colSpan={columns.length} className="px-4 py-6 text-center text-sm text-rose-600">
                    {resolveErrorMessage(filesQuery.error, 'Unable to load agent files right now.')}
                  </td>
                </tr>
              ) : treeRows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className="px-4 py-6 text-center text-sm text-slate-500">
                    No files yet. Upload files to get started.
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className={row.getIsSelected() ? 'bg-blue-50/50' : ''}>
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

      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="sr-only"
        onChange={handleFileChange}
      />
    </div>
  )
}
