import type { FormEvent } from 'react'
import { useState } from 'react'
import { Check, Mail, Phone } from 'lucide-react'
import { Checkbox as AriaCheckbox } from 'react-aria-components'

import { FormField, TextInput } from '../common/FormControls'
import { ModalForm } from '../common/ModalForm'
import type { AllowlistInput, AllowlistTableRow } from './contactTypes'

type AddContactModalProps = {
  onSubmit: (input: AllowlistInput) => Promise<void> | void
  onClose: () => void
}

type EditContactModalProps = AddContactModalProps & {
  contact: AllowlistTableRow
}

type ContactModalProps = AddContactModalProps & {
  contact?: AllowlistTableRow
}

function ContactModal({ contact, onSubmit, onClose }: ContactModalProps) {
  const editing = Boolean(contact)
  const channel = contact?.channel ?? 'email'
  const [address, setAddress] = useState(contact?.address ?? '')
  const [allowInbound, setAllowInbound] = useState(contact?.allowInbound ?? true)
  const [allowOutbound, setAllowOutbound] = useState(contact?.allowOutbound ?? true)
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
        channel,
        address: address.trim(),
        allowInbound,
        allowOutbound,
      })
      onClose()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : `Unable to ${editing ? 'update' : 'add'} contact.`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalForm
      id="allowlist-contact-form"
      title={editing ? 'Edit Contact' : 'Add Contact'}
      subtitle={editing
        ? 'Choose how this contact can communicate with the agent.'
        : "Add an email contact to this agent's allowlist."}
      onClose={onClose}
      onSubmit={handleSubmit}
      widthClass="sm:max-w-lg"
      icon={channel.toLowerCase() === 'sms' ? Phone : Mail}
      submitLabel={editing ? 'Apply Changes' : 'Add Contact'}
      submitting={submitting}
      submitDisabled={!address.trim() || Boolean(
        contact
          && contact.allowInbound === allowInbound
          && contact.allowOutbound === allowOutbound,
      )}
      errorMessages={error ? [error] : null}
      formClassName="space-y-5"
    >
      <FormField id="allowlist-contact-address" label={channel.toLowerCase() === 'sms' ? 'Phone number' : 'Email address'}>
        <TextInput
          id="allowlist-contact-address"
          type={channel.toLowerCase() === 'sms' ? 'tel' : 'email'}
          autoFocus={!editing}
          required
          value={address}
          onChange={(event) => setAddress(event.currentTarget.value)}
          placeholder={channel.toLowerCase() === 'sms' ? '+1 555 123 4567' : 'email@example.com'}
          disabled={submitting || editing}
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

export function AddContactModal(props: AddContactModalProps) {
  return <ContactModal {...props} />
}

export function EditContactModal({ contact, ...props }: EditContactModalProps) {
  return <ContactModal {...props} contact={contact} />
}
