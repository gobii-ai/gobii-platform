import type { FormEvent } from 'react'
import { useState } from 'react'
import { Mail, UserPlus } from 'lucide-react'

import { FormField, TextInput } from '../common/FormControls'
import { ModalForm } from '../common/ModalForm'

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
    <ModalForm
      id="collaborator-form"
      title="Invite Collaborator"
      subtitle="Invite an employee to chat and exchange files with this agent."
      onClose={onClose}
      onSubmit={handleSubmit}
      icon={UserPlus}
      iconBgClass="bg-emerald-100"
      iconColorClass="text-emerald-600"
      widthClass="sm:max-w-lg"
      submitLabel="Send Invite"
      submitting={submitting}
      submitDisabled={!email.trim()}
      errorMessages={error ? [error] : null}
      formClassName="space-y-5"
    >
        <FormField id="collaborator-email-field" label="Collaborator email">
          <div className="relative mt-1">
            <Mail className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
            <TextInput
              id="collaborator-email-field"
              type="email"
              autoFocus
              required
              value={email}
              onChange={(event) => setEmail(event.currentTarget.value)}
              className="mt-0 pl-9 focus:border-emerald-500 focus:ring-emerald-500"
              placeholder="name@company.com"
              disabled={submitting}
            />
          </div>
        </FormField>
    </ModalForm>
  )
}
