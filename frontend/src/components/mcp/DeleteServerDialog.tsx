import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'

import { deleteMcpServer } from '../../api/mcp'
import { HttpError } from '../../api/http'
import { ActionConfirmDialog } from '../common/ActionConfirmDialog'

type DeleteServerDialogProps = {
  serverName: string
  deleteUrl: string
  onClose: () => void
  onDeleted: () => void
  onError: (message: string) => void
}

export function DeleteServerDialog({ serverName, deleteUrl, onClose, onDeleted, onError }: DeleteServerDialogProps) {
  const [localError, setLocalError] = useState<string | null>(null)

  const deleteMutation = useMutation({
    mutationFn: (url: string) => deleteMcpServer(url),
  })

  const handleConfirm = async () => {
    setLocalError(null)
    try {
      await deleteMutation.mutateAsync(deleteUrl)
      onDeleted()
      onClose()
    } catch (error) {
      const message = resolveErrorMessage(error, 'Failed to delete MCP server.')
      setLocalError(message)
      onError(message)
    }
  }

  return (
    <ActionConfirmDialog
      open
      title="Delete MCP Server"
      description={`Are you sure you want to permanently delete ${serverName}? Linked agents will lose access immediately.`}
      onClose={onClose}
      onConfirm={handleConfirm}
      confirmLabel="Confirm Delete"
      busy={deleteMutation.isPending}
      danger
      icon={Trash2}
      localError={localError}
    >
      <p className="text-sm text-slate-600">
        This action cannot be undone.
      </p>
    </ActionConfirmDialog>
  )
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
