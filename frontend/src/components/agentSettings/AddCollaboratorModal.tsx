import type { FormEvent } from 'react'
import { useState } from 'react'
import { Mail, UserPlus } from 'lucide-react'

import { Modal } from '../common/Modal'

type AddCollaboratorModalProps = {
  onSubmit: (email: string) => Promise<void> | void
  onClose: () => void
}

export function AddCollaboratorModal({ onSubmit, onClose }: AddCollaboratorModalProps) {
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedEmail = email.trim().toLowerCase()
    if (!normalizedEmail) {
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(normalizedEmail)
      onClose()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Unable to send invite.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      title="Invite Collaborator"
      subtitle="Invite a coworker to chat and exchange files with this agent."
      onClose={onClose}
      icon={UserPlus}
      iconBgClass="bg-emerald-100"
      iconColorClass="text-emerald-600"
      widthClass="sm:max-w-lg"
    >
      <form className="space-y-5" onSubmit={handleSubmit}>
        <div>
          <label htmlFor="collaborator-email-field" className="block text-sm font-medium text-gray-700">
            Collaborator email
          </label>
          <div className="relative mt-1">
            <Mail className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
            <input
              id="collaborator-email-field"
              type="email"
              autoFocus
              required
              value={email}
              onChange={(event) => setEmail(event.currentTarget.value)}
              className="block w-full rounded-lg border border-gray-300 px-3 py-2 pl-9 text-sm shadow-sm focus:border-emerald-500 focus:ring-emerald-500"
              placeholder="name@company.com"
              disabled={submitting}
            />
          </div>
        </div>

        {error && <div className="text-sm text-rose-600">{error}</div>}

        <div className="flex items-center justify-end gap-3 pt-2">
          <button
            type="button"
            className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting || !email.trim()}
            className="px-4 py-2 text-sm font-medium text-white bg-emerald-600 rounded-lg shadow-sm hover:bg-emerald-700 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 disabled:opacity-60"
          >
            Send Invite
          </button>
        </div>
      </form>
    </Modal>
  )
}
