import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent, type KeyboardEvent, type MouseEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  type ColumnDef,
  type RowSelectionState,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { ArrowDownToLine, ArrowLeft, ArrowUp, ChevronRight, FileText, Folder, FolderPlus, RefreshCw, Trash2, UploadCloud } from 'lucide-react'

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
    createFolder: string
    move: string
  }
}

export type AgentFilesScreenProps = {
  initialData: AgentFilesPageData
}

type UploadPayload = {
  files: FileList
  parentId: string | null
}

type DeletePayload = {
  nodeIds: string[]
}

type CreateFolderPayload = {
  name: string
  parentId: string | null
}

type MovePayload = {
  nodeId: string
  parentId: string | null
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

function sortNodes(a: AgentFsNode, b: AgentFsNode): number {
  if (a.nodeType !== b.nodeType) {
    return a.nodeType === 'dir' ? -1 : 1
  }
  return a.name.localeCompare(b.name)
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

async function createFolder(url: string, payload: CreateFolderPayload): Promise<AgentFsNode> {
  const response = await jsonRequest<{ node: AgentFsNode }>(url, {
    method: 'POST',
    json: {
      name: payload.name,
      parentId: payload.parentId,
    },
    includeCsrf: true,
  })
  return response.node
}

async function moveNode(url: string, payload: MovePayload): Promise<AgentFsNode> {
  const response = await jsonRequest<{ node: AgentFsNode }>(url, {
    method: 'POST',
    json: {
      nodeId: payload.nodeId,
      parentId: payload.parentId,
    },
    includeCsrf: true,
  })
  return response.node
}

export function AgentFilesScreen({ initialData }: AgentFilesScreenProps) {
  const queryClient = useQueryClient()
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null)
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [pendingUploadParentId, setPendingUploadParentId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [isCreatingFolder, setIsCreatingFolder] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [dragOverNodeId, setDragOverNodeId] = useState<string | null>(null)
  const [uploadInfo, setUploadInfo] = useState<{ parentId: string | null; fileCount: number } | null>(null)
  const dragNodeRef = useRef<AgentFsNode | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const filesQuery = useQuery<AgentFilesResponse, Error>({
    queryKey: ['agent-files', initialData.agent.id],
    queryFn: ({ signal }) => jsonFetch<AgentFilesResponse>(initialData.urls.files, { signal }),
    refetchOnWindowFocus: false,
  })

  const nodes = filesQuery.data?.nodes ?? []
  const filespaceName = filesQuery.data?.filespace?.name ?? 'Agent Files'
  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])
  const childrenByParent = useMemo(() => {
    const map = new Map<string | null, AgentFsNode[]>()
    nodes.forEach((node) => {
      const key = node.parentId ?? null
      const current = map.get(key)
      if (current) {
        current.push(node)
      } else {
        map.set(key, [node])
      }
    })
    map.forEach((list) => list.sort(sortNodes))
    return map
  }, [nodes])

  const currentFolder = currentFolderId ? nodeMap.get(currentFolderId) ?? null : null
  const currentFolderPath = currentFolder?.path ?? '/'
  const currentRows = childrenByParent.get(currentFolderId) ?? []
  const parentFolderId = currentFolder?.parentId ?? null
  const parentFolderPath = parentFolderId ? nodeMap.get(parentFolderId)?.path ?? '/' : '/'
  const parentDropKey = currentFolderId ? (parentFolderId ?? 'root') : null
  const breadcrumbs = useMemo(() => {
    const trail: AgentFsNode[] = []
    let cursor: AgentFsNode | null = currentFolder
    while (cursor) {
      trail.unshift(cursor)
      cursor = cursor.parentId ? nodeMap.get(cursor.parentId) ?? null : null
    }
    return trail
  }, [currentFolder, nodeMap])

  const uploadMutation = useMutation({
    mutationFn: (payload: UploadPayload) => uploadFiles(initialData.urls.upload, payload),
    onMutate: (payload) => {
      setActionError(null)
      setUploadInfo({
        parentId: payload.parentId,
        fileCount: payload.files.length,
      })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agent-files', initialData.agent.id] })
    },
    onError: (error) => {
      setActionError(resolveErrorMessage(error, 'Failed to upload files.'))
    },
    onSettled: () => {
      setUploadInfo(null)
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

  const createFolderMutation = useMutation({
    mutationFn: (payload: CreateFolderPayload) => createFolder(initialData.urls.createFolder, payload),
    onSuccess: async () => {
      setActionError(null)
      setNewFolderName('')
      setIsCreatingFolder(false)
      await queryClient.invalidateQueries({ queryKey: ['agent-files', initialData.agent.id] })
    },
    onError: (error) => {
      setActionError(resolveErrorMessage(error, 'Failed to create folder.'))
    },
  })

  const moveMutation = useMutation({
    mutationFn: (payload: MovePayload) => moveNode(initialData.urls.move, payload),
    onSuccess: async () => {
      setActionError(null)
      setRowSelection({})
      await queryClient.invalidateQueries({ queryKey: ['agent-files', initialData.agent.id] })
    },
    onError: (error) => {
      setActionError(resolveErrorMessage(error, 'Failed to move item.'))
    },
  })

  useEffect(() => {
    if (currentFolderId && !nodeMap.has(currentFolderId)) {
      setCurrentFolderId(null)
    }
  }, [currentFolderId, nodeMap])

  useEffect(() => {
    setRowSelection({})
  }, [currentFolderId])

  const handleUploadClick = useCallback((parentId: string | null) => {
    setPendingUploadParentId(parentId)
    fileInputRef.current?.click()
  }, [])

  const handleFileChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files
      if (!files || files.length === 0) {
        setPendingUploadParentId(null)
        return
      }
      try {
        await uploadMutation.mutateAsync({
          files,
          parentId: pendingUploadParentId,
        })
      } catch (error) {
        // Errors are surfaced via mutation callbacks.
      }
      setPendingUploadParentId(null)
      event.target.value = ''
    },
    [pendingUploadParentId, uploadMutation],
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

  const handleCreateFolderSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      const trimmed = newFolderName.trim()
      if (!trimmed) {
        setActionError('Folder name is required.')
        return
      }
      try {
        await createFolderMutation.mutateAsync({
          name: trimmed,
          parentId: currentFolderId,
        })
      } catch (error) {
        // Errors are surfaced via mutation callbacks.
      }
    },
    [createFolderMutation, currentFolderId, newFolderName],
  )

  const handleOpenFolder = useCallback((node: AgentFsNode) => {
    if (node.nodeType !== 'dir') {
      return
    }
    setCurrentFolderId(node.id)
    setActionError(null)
  }, [])

  const handleFolderKeyDown = useCallback(
    (node: AgentFsNode, event: KeyboardEvent<HTMLDivElement>) => {
      if (node.nodeType !== 'dir') {
        return
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        handleOpenFolder(node)
      }
    },
    [handleOpenFolder],
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
      handleOpenFolder(node)
    },
    [handleOpenFolder],
  )

  const handleParentClick = useCallback(() => {
    setCurrentFolderId(parentFolderId ?? null)
  }, [parentFolderId])

  const handleNavigateTo = useCallback((folderId: string | null) => {
    setCurrentFolderId(folderId)
  }, [])

  const handleToggleCreateFolder = useCallback(() => {
    setIsCreatingFolder((current) => {
      const next = !current
      if (!next) {
        setNewFolderName('')
      }
      return next
    })
    setActionError(null)
  }, [])

  const handleParentDragOver = useCallback((event: DragEvent<HTMLTableRowElement>) => {
    event.preventDefault()
    const canCopy = Array.from(event.dataTransfer.types).includes('Files')
    event.dataTransfer.dropEffect = canCopy ? 'copy' : 'move'
  }, [])

  const handleParentDragEnter = useCallback(
    (event: DragEvent<HTMLTableRowElement>) => {
      if (!parentDropKey) {
        return
      }
      event.preventDefault()
      setDragOverNodeId(parentDropKey)
    },
    [parentDropKey],
  )

  const handleParentDragLeave = useCallback(
    (event: DragEvent<HTMLTableRowElement>) => {
      if (!parentDropKey) {
        return
      }
      const nextTarget = event.relatedTarget as Node | null
      if (nextTarget && event.currentTarget.contains(nextTarget)) {
        return
      }
      setDragOverNodeId((prev) => (prev === parentDropKey ? null : prev))
    },
    [parentDropKey],
  )

  const handleParentDrop = useCallback(
    async (event: DragEvent<HTMLTableRowElement>) => {
      event.preventDefault()
      event.stopPropagation()
      setDragOverNodeId(null)
      const files = event.dataTransfer.files
      const targetParentId = parentFolderId ?? null
      if (files && files.length > 0) {
        try {
          await uploadMutation.mutateAsync({ files, parentId: targetParentId })
        } catch (error) {
          // Errors are surfaced via mutation callbacks.
        }
        return
      }
      const draggedNode = dragNodeRef.current
      if (!draggedNode) {
        return
      }
      if (draggedNode.parentId === targetParentId) {
        return
      }
      try {
        await moveMutation.mutateAsync({ nodeId: draggedNode.id, parentId: targetParentId })
      } catch (error) {
        // Errors are surfaced via mutation callbacks.
      }
    },
    [moveMutation, parentFolderId, uploadMutation],
  )

  const handleDragStart = useCallback((node: AgentFsNode, event: DragEvent<HTMLElement>) => {
    dragNodeRef.current = node
    event.dataTransfer.setData('text/plain', node.id)
    event.dataTransfer.effectAllowed = 'move'
  }, [])

  const handleDragEnd = useCallback(() => {
    dragNodeRef.current = null
    setDragOverNodeId(null)
  }, [])

  const handleFolderDragOver = useCallback((node: AgentFsNode, event: DragEvent<HTMLElement>) => {
    if (node.nodeType !== 'dir') {
      return
    }
    event.preventDefault()
    const canCopy = Array.from(event.dataTransfer.types).includes('Files')
    event.dataTransfer.dropEffect = canCopy ? 'copy' : 'move'
  }, [])

  const handleFolderDragEnter = useCallback((node: AgentFsNode, event: DragEvent<HTMLElement>) => {
    if (node.nodeType !== 'dir') {
      return
    }
    event.preventDefault()
    setDragOverNodeId(node.id)
  }, [])

  const handleFolderDragLeave = useCallback((node: AgentFsNode, event: DragEvent<HTMLElement>) => {
    if (node.nodeType !== 'dir') {
      return
    }
    const nextTarget = event.relatedTarget as Node | null
    if (nextTarget && event.currentTarget.contains(nextTarget)) {
      return
    }
    setDragOverNodeId((prev) => (prev === node.id ? null : prev))
  }, [])

  const handleFolderDrop = useCallback(
    async (node: AgentFsNode, event: DragEvent<HTMLElement>) => {
      if (node.nodeType !== 'dir') {
        return
      }
      event.preventDefault()
      event.stopPropagation()
      setDragOverNodeId(null)
      const files = event.dataTransfer.files
      if (files && files.length > 0) {
        try {
          await uploadMutation.mutateAsync({ files, parentId: node.id })
        } catch (error) {
          // Errors are surfaced via mutation callbacks.
        }
        return
      }
      const draggedNode = dragNodeRef.current
      if (!draggedNode || draggedNode.id === node.id) {
        return
      }
      if (draggedNode.parentId === node.id) {
        return
      }
      try {
        await moveMutation.mutateAsync({ nodeId: draggedNode.id, parentId: node.id })
      } catch (error) {
        // Errors are surfaced via mutation callbacks.
      }
    },
    [moveMutation, uploadMutation],
  )

  const handleCurrentFolderDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    const canCopy = Array.from(event.dataTransfer.types).includes('Files')
    event.dataTransfer.dropEffect = canCopy ? 'copy' : 'move'
  }, [])

  const handleCurrentFolderDrop = useCallback(
    async (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      setDragOverNodeId(null)
      const files = event.dataTransfer.files
      if (files && files.length > 0) {
        try {
          await uploadMutation.mutateAsync({ files, parentId: currentFolderId })
        } catch (error) {
          // Errors are surfaced via mutation callbacks.
        }
        return
      }
      const draggedNode = dragNodeRef.current
      if (!draggedNode) {
        return
      }
      if (draggedNode.parentId === currentFolderId) {
        return
      }
      try {
        await moveMutation.mutateAsync({ nodeId: draggedNode.id, parentId: currentFolderId })
      } catch (error) {
        // Errors are surfaced via mutation callbacks.
      }
    },
    [currentFolderId, moveMutation, uploadMutation],
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
                onClick={isDir ? () => handleOpenFolder(row.original) : undefined}
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
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 transition hover:bg-blue-100"
                onClick={() => handleUploadClick(node.id)}
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
  }, [handleFolderKeyDown, handleSingleDelete, handleUploadClick, initialData.urls.download])

  const table = useReactTable({
    data: currentRows,
    columns,
    state: { rowSelection },
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    enableRowSelection: (row) => row.original.nodeType === 'file',
  })

  const handleRefresh = useCallback(() => {
    filesQuery.refetch().catch(() => {})
  }, [filesQuery])

  const currentFolderLabel = `Folder: ${currentFolderPath}`
  const isBusy = uploadMutation.isPending || deleteMutation.isPending || createFolderMutation.isPending || moveMutation.isPending
  const uploadTargetName = uploadInfo
    ? uploadInfo.parentId
      ? nodeMap.get(uploadInfo.parentId)?.path ?? 'Folder'
      : '/'
    : null

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
              onClick={() => handleUploadClick(currentFolderId)}
              disabled={isBusy}
            >
              <UploadCloud className="h-4 w-4" aria-hidden="true" />
              Upload Files
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-60"
              onClick={handleToggleCreateFolder}
              disabled={isBusy}
            >
              <FolderPlus className="h-4 w-4" aria-hidden="true" />
              {isCreatingFolder ? 'Cancel' : 'New Folder'}
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
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">{currentFolderLabel}</span>
            </div>
          </div>
          {uploadMutation.isPending && uploadInfo ? (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-700">
              <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span>
                Uploading {uploadInfo.fileCount} file{uploadInfo.fileCount === 1 ? '' : 's'} to {uploadTargetName}
              </span>
              <span className="h-1.5 w-32 overflow-hidden rounded-full bg-blue-200">
                <span className="block h-full w-1/2 animate-pulse rounded-full bg-blue-600" />
              </span>
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <button
              type="button"
              className="font-semibold text-blue-700 transition hover:text-blue-900 disabled:text-slate-400"
              onClick={() => handleNavigateTo(null)}
              disabled={!breadcrumbs.length}
            >
              Root
            </button>
            {breadcrumbs.map((folder) => (
              <span key={folder.id} className="inline-flex items-center gap-2">
                <ChevronRight className="h-4 w-4 text-slate-400" aria-hidden="true" />
                <button
                  type="button"
                  className="font-semibold text-blue-700 transition hover:text-blue-900"
                  onClick={() => handleNavigateTo(folder.id)}
                >
                  {folder.name}
                </button>
              </span>
            ))}
          </div>
          {isCreatingFolder ? (
            <form className="flex flex-wrap items-center gap-2" onSubmit={handleCreateFolderSubmit}>
              <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
                <FolderPlus className="h-4 w-4 text-emerald-600" aria-hidden="true" />
                <input
                  type="text"
                  name="folderName"
                  value={newFolderName}
                  onChange={(event) => {
                    setNewFolderName(event.target.value)
                    setActionError(null)
                  }}
                  autoFocus
                  className="flex-1 bg-white text-sm text-slate-700 outline-none"
                  placeholder="New folder name"
                />
              </div>
              <button
                type="submit"
                className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-60"
                disabled={isBusy}
              >
                Create folder
              </button>
            </form>
          ) : null}
          {actionError && <p className="text-sm text-rose-600">{actionError}</p>}
        </div>

        <div className="overflow-x-auto" onDragOver={handleCurrentFolderDragOver} onDrop={handleCurrentFolderDrop}>
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
              ) : (
                <>
                  {currentFolderId ? (
                    <tr
                      className={[
                        'cursor-pointer bg-blue-50/40',
                        dragOverNodeId === parentDropKey ? 'bg-blue-100/70' : '',
                      ].join(' ')}
                      onClick={handleParentClick}
                      onDragOver={handleParentDragOver}
                      onDragEnter={handleParentDragEnter}
                      onDragLeave={handleParentDragLeave}
                      onDrop={handleParentDrop}
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
                  {currentRows.length === 0 ? (
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
                          dragOverNodeId === row.original.id ? 'bg-blue-100/70' : '',
                        ].join(' ')}
                        draggable={!isBusy}
                        onDoubleClick={(event) => handleRowDoubleClick(row.original, event)}
                        onDragStart={(event) => handleDragStart(row.original, event)}
                        onDragEnd={handleDragEnd}
                        onDragOver={(event) => handleFolderDragOver(row.original, event)}
                        onDragEnter={(event) => handleFolderDragEnter(row.original, event)}
                        onDragLeave={(event) => handleFolderDragLeave(row.original, event)}
                        onDrop={(event) => handleFolderDrop(row.original, event)}
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
