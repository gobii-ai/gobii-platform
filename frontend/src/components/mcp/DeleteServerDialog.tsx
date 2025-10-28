import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'

import { deleteMcpServer } from '../../api/mcp'
import { HttpError } from '../../api/http'
import { Modal } from '../common/Modal'

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

  const footer = (
    <>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-red-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        onClick={handleConfirm}
        disabled={deleteMutation.isPending}
      >
        {deleteMutation.isPending ? 'Deleting…' : 'Confirm Delete'}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        onClick={onClose}
        disabled={deleteMutation.isPending}
      >
        Cancel
      </button>
    </>
  )

  return (
    <Modal
      title="Delete MCP Server"
      subtitle={`Are you sure you want to permanently delete ${serverName}? Linked agents will lose access immediately.`}
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-lg"
      icon={Trash2}
      iconBgClass="bg-red-100"
      iconColorClass="text-red-600"
    >
      <div className="space-y-3">
        <p className="text-sm text-slate-600">
          This action cannot be undone.
        </p>
        {localError && <p className="text-xs text-red-600">{localError}</p>}
      </div>
    </Modal>
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
