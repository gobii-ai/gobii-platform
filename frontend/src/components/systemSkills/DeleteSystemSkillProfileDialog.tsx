import { useState } from 'react'
import { Trash2 } from 'lucide-react'

import { HttpError } from '../../api/http'
import { Modal } from '../common/Modal'


type DeleteSystemSkillProfileDialogProps = {
  profileLabel: string
  onClose: () => void
  onConfirm: () => Promise<void>
}


export function DeleteSystemSkillProfileDialog({
  profileLabel,
  onClose,
  onConfirm,
}: DeleteSystemSkillProfileDialogProps) {
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
            : 'Failed to delete profile.'
      setLocalError(message)
    } finally {
      setBusy(false)
    }
  }

  const footer = (
    <>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-red-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        onClick={handleConfirm}
        disabled={busy}
      >
        {busy ? 'Deleting\u2026' : 'Confirm Delete'}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        onClick={onClose}
        disabled={busy}
      >
        Cancel
      </button>
    </>
  )

  return (
    <Modal
      title="Delete Profile"
      subtitle={`Are you sure you want to delete "${profileLabel}"? This action cannot be undone.`}
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-lg"
      icon={Trash2}
      iconBgClass="bg-red-100"
      iconColorClass="text-red-600"
    >
      <div className="space-y-3">
        <p className="text-sm text-slate-600">
          Agents will no longer be able to use this credential profile.
        </p>
        {localError && <p className="text-xs text-red-600">{localError}</p>}
      </div>
    </Modal>
  )
}
