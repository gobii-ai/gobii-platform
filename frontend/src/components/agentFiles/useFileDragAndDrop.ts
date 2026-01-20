import { useCallback, useRef, useState } from 'react'
import type { DragEvent } from 'react'

import type { AgentFsNode } from './types'

type UploadHandler = (files: FileList, parentId: string | null) => Promise<void>
type MoveHandler = (nodeId: string, parentId: string | null) => Promise<void>

export type FileDragAndDropHandlers = {
  dragOverNodeId: string | null
  parentDropKey: string | null
  onParentDragOver: (event: DragEvent<HTMLTableRowElement>) => void
  onParentDragEnter: (event: DragEvent<HTMLTableRowElement>) => void
  onParentDragLeave: (event: DragEvent<HTMLTableRowElement>) => void
  onParentDrop: (event: DragEvent<HTMLTableRowElement>) => void
  onRowDragStart: (node: AgentFsNode, event: DragEvent<HTMLElement>) => void
  onRowDragEnd: () => void
  onFolderDragOver: (node: AgentFsNode, event: DragEvent<HTMLElement>) => void
  onFolderDragEnter: (node: AgentFsNode, event: DragEvent<HTMLElement>) => void
  onFolderDragLeave: (node: AgentFsNode, event: DragEvent<HTMLElement>) => void
  onFolderDrop: (node: AgentFsNode, event: DragEvent<HTMLElement>) => void
  onCurrentFolderDragOver: (event: DragEvent<HTMLDivElement>) => void
  onCurrentFolderDrop: (event: DragEvent<HTMLDivElement>) => void
}

type UseFileDragAndDropProps = {
  currentFolderId: string | null
  parentFolderId: string | null
  onUploadFiles: UploadHandler
  onMoveNode: MoveHandler
  allowMove?: boolean
}

export function useFileDragAndDrop({
  currentFolderId,
  parentFolderId,
  onUploadFiles,
  onMoveNode,
  allowMove = true,
}: UseFileDragAndDropProps): FileDragAndDropHandlers {
  const dragNodeRef = useRef<AgentFsNode | null>(null)
  const [dragOverNodeId, setDragOverNodeId] = useState<string | null>(null)
  const parentDropKey = currentFolderId ? (parentFolderId ?? 'root') : null

  const handleParentDragOver = useCallback((event: DragEvent<HTMLTableRowElement>) => {
    const canCopy = Array.from(event.dataTransfer.types).includes('Files')
    if (!allowMove && !canCopy) {
      return
    }
    event.preventDefault()
    event.dataTransfer.dropEffect = canCopy ? 'copy' : 'move'
  }, [allowMove])

  const handleParentDragEnter = useCallback(
    (event: DragEvent<HTMLTableRowElement>) => {
      if (!parentDropKey) {
        return
      }
      const canCopy = Array.from(event.dataTransfer.types).includes('Files')
      if (!allowMove && !canCopy) {
        return
      }
      event.preventDefault()
      setDragOverNodeId(parentDropKey)
    },
    [allowMove, parentDropKey],
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
          await onUploadFiles(files, targetParentId)
        } catch {
          // Errors are surfaced elsewhere.
        }
        return
      }
      if (!allowMove) {
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
        await onMoveNode(draggedNode.id, targetParentId)
      } catch {
        // Errors are surfaced elsewhere.
      }
    },
    [allowMove, onMoveNode, onUploadFiles, parentFolderId],
  )

  const handleRowDragStart = useCallback((node: AgentFsNode, event: DragEvent<HTMLElement>) => {
    if (!allowMove) {
      return
    }
    dragNodeRef.current = node
    event.dataTransfer.setData('text/plain', node.id)
    event.dataTransfer.effectAllowed = 'move'
  }, [allowMove])

  const handleRowDragEnd = useCallback(() => {
    dragNodeRef.current = null
    setDragOverNodeId(null)
  }, [])

  const handleFolderDragOver = useCallback((node: AgentFsNode, event: DragEvent<HTMLElement>) => {
    if (node.nodeType !== 'dir') {
      return
    }
    const canCopy = Array.from(event.dataTransfer.types).includes('Files')
    if (!allowMove && !canCopy) {
      return
    }
    event.preventDefault()
    event.dataTransfer.dropEffect = canCopy ? 'copy' : 'move'
  }, [allowMove])

  const handleFolderDragEnter = useCallback((node: AgentFsNode, event: DragEvent<HTMLElement>) => {
    if (node.nodeType !== 'dir') {
      return
    }
    const canCopy = Array.from(event.dataTransfer.types).includes('Files')
    if (!allowMove && !canCopy) {
      return
    }
    event.preventDefault()
    setDragOverNodeId(node.id)
  }, [allowMove])

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
          await onUploadFiles(files, node.id)
        } catch {
          // Errors are surfaced elsewhere.
        }
        return
      }
      if (!allowMove) {
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
        await onMoveNode(draggedNode.id, node.id)
      } catch {
        // Errors are surfaced elsewhere.
      }
    },
    [allowMove, onMoveNode, onUploadFiles],
  )

  const handleCurrentFolderDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    const canCopy = Array.from(event.dataTransfer.types).includes('Files')
    if (!allowMove && !canCopy) {
      return
    }
    event.preventDefault()
    event.dataTransfer.dropEffect = canCopy ? 'copy' : 'move'
  }, [allowMove])

  const handleCurrentFolderDrop = useCallback(
    async (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      setDragOverNodeId(null)
      const files = event.dataTransfer.files
      if (files && files.length > 0) {
        try {
          await onUploadFiles(files, currentFolderId)
        } catch {
          // Errors are surfaced elsewhere.
        }
        return
      }
      if (!allowMove) {
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
        await onMoveNode(draggedNode.id, currentFolderId)
      } catch {
        // Errors are surfaced elsewhere.
      }
    },
    [allowMove, currentFolderId, onMoveNode, onUploadFiles],
  )

  return {
    dragOverNodeId,
    parentDropKey,
    onParentDragOver: handleParentDragOver,
    onParentDragEnter: handleParentDragEnter,
    onParentDragLeave: handleParentDragLeave,
    onParentDrop: handleParentDrop,
    onRowDragStart: handleRowDragStart,
    onRowDragEnd: handleRowDragEnd,
    onFolderDragOver: handleFolderDragOver,
    onFolderDragEnter: handleFolderDragEnter,
    onFolderDragLeave: handleFolderDragLeave,
    onFolderDrop: handleFolderDrop,
    onCurrentFolderDragOver: handleCurrentFolderDragOver,
    onCurrentFolderDrop: handleCurrentFolderDrop,
  }
}
