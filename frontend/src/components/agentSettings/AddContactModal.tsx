import type { FormEvent } from 'react'
import { useState } from 'react'
import { Check, Mail } from 'lucide-react'
import { Checkbox as AriaCheckbox } from 'react-aria-components'

import { FormField, TextInput } from '../common/FormControls'
import { ModalForm } from '../common/ModalForm'
import type { AllowlistInput } from './contactTypes'

type AddContactModalProps = {
  onSubmit: (input: AllowlistInput) => Promise<void> | void
  onClose: () => void
}

export function AddContactModal({ onSubmit, onClose }: AddContactModalProps) {
  const [address, setAddress] = useState('')
  const [allowInbound, setAllowInbound] = useState(true)
  const [allowOutbound, setAllowOutbound] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!address.trim()) {
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({
        channel: 'email',
        address: address.trim(),
        allowInbound,
        allowOutbound,
      })
      onClose()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Unable to add contact.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalForm
      id="allowlist-contact-form"
      title="Add Contact"
      subtitle="Add an email contact to this agent's allowlist."
      onClose={onClose}
      onSubmit={handleSubmit}
      widthClass="sm:max-w-lg"
      icon={Mail}
      submitLabel="Add Contact"
      submitting={submitting}
      submitDisabled={!address.trim()}
      errorMessages={error ? [error] : null}
      formClassName="space-y-5"
    >
        <FormField id="allowlist-contact-address" label="Email address">
          <TextInput
            id="allowlist-contact-address"
            type="email"
            autoFocus
            required
            value={address}
            onChange={(event) => setAddress(event.currentTarget.value)}
            placeholder="email@example.com"
            disabled={submitting}
          />
        </FormField>

        <div className="grid gap-3 sm:grid-cols-2">
          <AriaCheckbox
            isSelected={allowInbound}
            onChange={setAllowInbound}
            isDisabled={submitting}
            className="group inline-flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50/60 px-4 py-3 text-sm text-slate-700"
          >
            {({ isSelected }) => (
              <>
                <span
                  aria-hidden="true"
                  className={`mt-0.5 flex h-4 w-4 items-center justify-center rounded border transition ${
                    isSelected ? 'border-emerald-600 bg-emerald-600 text-white' : 'border-emerald-300 bg-white text-transparent'
                  }`}
                >
                  <Check className="h-3 w-3" aria-hidden="true" />
                </span>
                <span className="flex flex-col leading-tight">
                  <span className="font-medium text-slate-800">Allow inbound</span>
                  <span className="text-xs text-slate-600">This contact can send messages to the agent.</span>
                </span>
              </>
            )}
          </AriaCheckbox>

          <AriaCheckbox
            isSelected={allowOutbound}
            onChange={setAllowOutbound}
            isDisabled={submitting}
            className="group inline-flex items-start gap-3 rounded-xl border border-sky-200 bg-sky-50/60 px-4 py-3 text-sm text-slate-700"
          >
            {({ isSelected }) => (
              <>
                <span
                  aria-hidden="true"
                  className={`mt-0.5 flex h-4 w-4 items-center justify-center rounded border transition ${
                    isSelected ? 'border-sky-600 bg-sky-600 text-white' : 'border-sky-300 bg-white text-transparent'
                  }`}
                >
                  <Check className="h-3 w-3" aria-hidden="true" />
                </span>
                <span className="flex flex-col leading-tight">
                  <span className="font-medium text-slate-800">Allow outbound</span>
                  <span className="text-xs text-slate-600">The agent can send messages to this contact.</span>
                </span>
              </>
            )}
          </AriaCheckbox>
        </div>
    </ModalForm>
  )
}
