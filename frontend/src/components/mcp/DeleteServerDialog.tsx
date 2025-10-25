import { Trash2 } from 'lucide-react'

import { Modal } from '../common/Modal'

type DeleteServerDialogProps = {
  serverName: string
  onConfirm: () => void
  onCancel: () => void
  isDeleting: boolean
}

export function DeleteServerDialog({ serverName, onConfirm, onCancel, isDeleting }: DeleteServerDialogProps) {
  const footer = (
    <>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-red-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        onClick={onConfirm}
        disabled={isDeleting}
      >
        {isDeleting ? 'Deleting…' : 'Confirm Delete'}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        onClick={onCancel}
        disabled={isDeleting}
      >
        Cancel
      </button>
    </>
  )

  return (
    <Modal
      title="Delete MCP Server"
      subtitle={`Are you sure you want to permanently delete ${serverName}? Linked agents will lose access immediately.`}
      onClose={onCancel}
      footer={footer}
      widthClass="sm:max-w-lg"
      icon={Trash2}
      iconBgClass="bg-red-100"
      iconColorClass="text-red-600"
    >
      <p className="text-sm text-slate-600">
        This action cannot be undone. If you just need to pause access, edit the server and toggle it inactive.
      </p>
    </Modal>
  )
}
