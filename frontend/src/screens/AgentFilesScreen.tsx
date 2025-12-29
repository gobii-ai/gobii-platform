import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { RowSelectionState } from '@tanstack/react-table'
import { RefreshCw } from 'lucide-react'

import { HttpError, getCsrfToken, jsonFetch, jsonRequest } from '../api/http'
import { CreateFolderForm } from '../components/agentFiles/CreateFolderForm'
import { FileManagerBreadcrumbs } from '../components/agentFiles/FileManagerBreadcrumbs'
import { FileManagerHeader } from '../components/agentFiles/FileManagerHeader'
import { FileTable } from '../components/agentFiles/FileTable'
import type { AgentFilesPageData, AgentFilesResponse, AgentFsNode } from '../components/agentFiles/types'
import { useFileDragAndDrop } from '../components/agentFiles/useFileDragAndDrop'
import { sortNodes } from '../components/agentFiles/utils'

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
    } catch {
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
  const [actionError, setActionError] = useState<string | null>(null)
  const [isCreatingFolder, setIsCreatingFolder] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [uploadInfo, setUploadInfo] = useState<{ parentId: string | null; fileCount: number } | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const emptyNodesRef = useRef<AgentFsNode[]>([])
  const pendingUploadParentIdRef = useRef<string | null>(null)
  const uploadInputId = 'agent-files-upload-input'

  const filesQuery = useQuery<AgentFilesResponse, Error>({
    queryKey: ['agent-files', initialData.agent.id],
    queryFn: ({ signal }) => jsonFetch<AgentFilesResponse>(initialData.urls.files, { signal }),
    refetchOnWindowFocus: false,
  })

  const nodes = filesQuery.data?.nodes ?? emptyNodesRef.current
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
  const currentRows = childrenByParent.get(currentFolderId) ?? emptyNodesRef.current
  const parentFolderId = currentFolder?.parentId ?? null
  const parentFolderPath = parentFolderId ? nodeMap.get(parentFolderId)?.path ?? '/' : '/'
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

  const handleUploadRequest = useCallback((parentId: string | null) => {
    pendingUploadParentIdRef.current = parentId
  }, [])

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
    } catch {
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
    } catch {
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
      } catch {
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

  const handleFolderNameChange = useCallback((value: string) => {
    setNewFolderName(value)
    setActionError(null)
  }, [])

  const handleUpload = useCallback(
    async (files: FileList, parentId: string | null) => {
      try {
        await uploadMutation.mutateAsync({ files, parentId })
      } catch {
        // Errors are surfaced via mutation callbacks.
      }
    },
    [uploadMutation],
  )

  const handleFileChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files
      if (!files || files.length === 0) {
        pendingUploadParentIdRef.current = null
        return
      }
      try {
        await handleUpload(files, pendingUploadParentIdRef.current)
      } catch {
        // Errors are surfaced via mutation callbacks.
      }
      pendingUploadParentIdRef.current = null
      event.target.value = ''
    },
    [handleUpload],
  )

  const handleMove = useCallback(
    async (nodeId: string, parentId: string | null) => {
      try {
        await moveMutation.mutateAsync({ nodeId, parentId })
      } catch {
        // Errors are surfaced via mutation callbacks.
      }
    },
    [moveMutation],
  )

  const triggerUploadInput = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const dragAndDrop = useFileDragAndDrop({
    currentFolderId,
    parentFolderId,
    onUploadFiles: handleUpload,
    onMoveNode: handleMove,
  })

  const isBusy = uploadMutation.isPending || deleteMutation.isPending || createFolderMutation.isPending || moveMutation.isPending
  const errorMessage = filesQuery.isError
    ? resolveErrorMessage(filesQuery.error, 'Unable to load agent files right now.')
    : null

  const handleRefresh = useCallback(() => {
    filesQuery.refetch().catch(() => {})
  }, [filesQuery])
  const uploadTargetName = uploadInfo
    ? uploadInfo.parentId
      ? nodeMap.get(uploadInfo.parentId)?.path ?? 'Folder'
      : '/'
    : null

  return (
    <div className="space-y-6 pb-6">
      <div className="gobii-card-base overflow-hidden">
        <FileManagerHeader
          agentName={initialData.agent.name}
          agentDetailUrl={initialData.urls.agentDetail}
          uploadInputId={uploadInputId}
          isBusy={isBusy}
          isCreatingFolder={isCreatingFolder}
          selectedRows={selectedRows}
          isRefreshing={filesQuery.isFetching}
          onUploadRequest={() => handleUploadRequest(currentFolderId)}
          onTriggerUploadInput={triggerUploadInput}
          onToggleCreateFolder={handleToggleCreateFolder}
          onBulkDelete={handleBulkDelete}
          onRefresh={handleRefresh}
        />

        <div className="flex flex-col gap-3 px-6 py-4">
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
          <FileManagerBreadcrumbs breadcrumbs={breadcrumbs} onNavigate={handleNavigateTo} />
          {isCreatingFolder ? (
            <CreateFolderForm
              folderName={newFolderName}
              isBusy={isBusy}
              onNameChange={handleFolderNameChange}
              onSubmit={handleCreateFolderSubmit}
            />
          ) : null}
          {actionError && <p className="text-sm text-rose-600">{actionError}</p>}
        </div>

        <FileTable
          rows={currentRows}
          isBusy={isBusy}
          isLoading={filesQuery.isPending}
          errorMessage={errorMessage}
          currentFolderId={currentFolderId}
          parentFolderPath={parentFolderPath}
          rowSelection={rowSelection}
          onRowSelectionChange={setRowSelection}
          onNavigateToParent={handleParentClick}
          onOpenFolder={handleOpenFolder}
          onRequestUpload={handleUploadRequest}
          onTriggerUploadInput={triggerUploadInput}
          onDeleteNode={handleSingleDelete}
          downloadBaseUrl={initialData.urls.download}
          uploadInputId={uploadInputId}
          dragAndDrop={dragAndDrop}
        />
      </div>

      <input
        id={uploadInputId}
        ref={fileInputRef}
        type="file"
        multiple
        className="sr-only"
        onChange={handleFileChange}
      />
    </div>
  )
}
