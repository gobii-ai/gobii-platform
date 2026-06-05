import { useState } from 'react'
import { Trash2 } from 'lucide-react'

import { HttpError } from '../../api/http'
import { ActionConfirmDialog } from '../common/ActionConfirmDialog'

type DeleteSecretDialogProps = {
  secretName: string
  onClose: () => void
  onConfirm: () => Promise<void>
}

export function DeleteSecretDialog({ secretName, onClose, onConfirm }: DeleteSecretDialogProps) {
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  const handleConfirm = async () => {
    setBusy(true)
    setLocalError(null)
    try {
      await onConfirm()
      onClose()
    } catch (error) {
      const message =
        error instanceof HttpError
          ? (typeof error.body === 'object' && error.body && 'error' in (error.body as Record<string, unknown>)
              ? String((error.body as Record<string, unknown>).error)
              : error.statusText)
          : error instanceof Error
            ? error.message
            : 'Failed to delete secret.'
      setLocalError(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ActionConfirmDialog
      open
      title="Delete Secret"
      description={`Are you sure you want to permanently delete "${secretName}"? This action cannot be undone.`}
      onClose={onClose}
      onConfirm={handleConfirm}
      confirmLabel="Confirm Delete"
      busy={busy}
      danger
      icon={Trash2}
      localError={localError}
    >
      <p className="text-sm text-slate-600">
        Any agents using this secret will no longer have access to it.
      </p>
    </ActionConfirmDialog>
  )
}
