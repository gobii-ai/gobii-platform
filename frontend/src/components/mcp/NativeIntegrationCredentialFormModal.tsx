import { useState, type FormEvent } from 'react'
import { ExternalLink, KeyRound, Loader2 } from 'lucide-react'

import { HttpError } from '../../api/http'
import type { NativeIntegrationProvider } from '../../api/nativeIntegrations'
import { Modal } from '../common/Modal'
import { NativeProviderIcon } from './NativeIntegrationShared'

type NativeIntegrationCredentialFormModalProps = {
  provider: NativeIntegrationProvider
  onClose: () => void
  onSubmit: (credentials: Record<string, string | null>) => Promise<unknown>
}

function parseFormErrors(error: unknown): Record<string, string[]> {
  if (error instanceof HttpError && typeof error.body === 'object' && error.body) {
    const body = error.body as Record<string, unknown>
    if (body.errors && typeof body.errors === 'object') {
      const parsed: Record<string, string[]> = {}
      for (const [field, messages] of Object.entries(body.errors as Record<string, unknown>)) {
        parsed[field] = Array.isArray(messages) ? messages.map(String) : [String(messages)]
      }
      return parsed
    }
    return { __all__: [body.error ? String(body.error) : 'Unable to save credentials.'] }
  }
  return { __all__: [error instanceof Error ? error.message : 'Unable to save credentials.'] }
}

export function NativeIntegrationCredentialFormModal({
  provider,
  onClose,
  onSubmit,
}: NativeIntegrationCredentialFormModalProps) {
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({})
  const [clearFields, setClearFields] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string[]>>({})

  const presentFields = new Set(provider.presentCredentialFields)

  const handleFieldChange = (key: string, value: string) => {
    setFieldValues((current) => ({ ...current, [key]: value }))
    if (value && clearFields[key]) {
      setClearFields((current) => ({ ...current, [key]: false }))
    }
  }

  const handleClearChange = (key: string, checked: boolean) => {
    setClearFields((current) => ({ ...current, [key]: checked }))
    if (checked) {
      setFieldValues((current) => ({ ...current, [key]: '' }))
    }
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setErrors({})

    const credentials: Record<string, string | null> = {}
    for (const field of provider.credentialFields) {
      const nextValue = (fieldValues[field.key] ?? '').trim()
      if (clearFields[field.key]) {
        credentials[field.key] = null
      } else if (nextValue) {
        credentials[field.key] = nextValue
      }
    }

    try {
      await onSubmit(credentials)
      onClose()
    } catch (error) {
      setErrors(parseFormErrors(error))
    } finally {
      setBusy(false)
    }
  }

  const allErrors = Object.values(errors).flat()

  const footer = (
    <>
      <button
        type="submit"
        form="native-integration-credential-form"
        className="inline-flex w-full justify-center gap-2 rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60 sm:ml-3 sm:w-auto sm:text-sm"
        disabled={busy}
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
        {busy ? 'Saving...' : 'Save Credentials'}
      </button>
      <button
        type="button"
        className="inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-60 sm:ml-3 sm:w-auto sm:text-sm"
        onClick={onClose}
        disabled={busy}
      >
        Cancel
      </button>
    </>
  )

  return (
    <Modal
      title={`Connect ${provider.displayName}`}
      subtitle="Enter the workspace credentials this native integration should use."
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-2xl"
      icon={KeyRound}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
    >
      <form id="native-integration-credential-form" onSubmit={handleSubmit} className="space-y-4" autoComplete="off">
        <div className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-3">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50">
            <NativeProviderIcon provider={provider} framed />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-900">{provider.displayName}</p>
            {provider.description ? <p className="text-sm text-slate-600">{provider.description}</p> : null}
          </div>
        </div>

        {allErrors.length > 0 ? (
          <div className="rounded-md bg-red-50 p-3">
            {allErrors.map((message, index) => (
              <p key={index} className="text-sm text-red-700">
                {message}
              </p>
            ))}
          </div>
        ) : null}

        <div className="space-y-4">
          {provider.credentialFields.map((field) => {
            const isPresent = presentFields.has(field.key)
            const canClear = isPresent && !field.required
            const disabled = Boolean(clearFields[field.key])
            return (
              <div key={field.key} className="rounded-lg border border-slate-200 p-4">
                <label htmlFor={`native-field-${field.key}`} className="block text-sm font-medium text-slate-700">
                  {field.name}
                  {field.required ? ' *' : ''}
                </label>
                {field.description ? <p className="mt-1 text-xs text-slate-500">{field.description}</p> : null}
                {field.howToGet ? (
                  <div className="mt-3 rounded-lg bg-blue-50 px-3 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-blue-700">How To Get This</p>
                    <p className="mt-1 text-sm text-blue-900">{field.howToGet}</p>
                    {field.docs.length > 0 ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {field.docs.map((doc) => (
                          <a
                            key={`${field.key}-${doc.url}`}
                            href={doc.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 rounded-full bg-white px-2.5 py-1 text-xs font-medium text-blue-700 transition hover:bg-blue-100"
                          >
                            {doc.title}
                            <ExternalLink className="h-3 w-3" aria-hidden="true" />
                          </a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                <input
                  id={`native-field-${field.key}`}
                  type="password"
                  value={fieldValues[field.key] ?? ''}
                  onChange={(event) => handleFieldChange(field.key, event.target.value)}
                  required={!isPresent && field.required && !field.default}
                  className="mt-2 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500 disabled:bg-slate-100"
                  placeholder={
                    isPresent
                      ? 'Leave blank to keep the saved value'
                      : field.default
                        ? `Optional, defaults to ${field.default}`
                        : 'Enter a value'
                  }
                  autoComplete="new-password"
                  disabled={disabled}
                />
                <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                  {isPresent ? <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700">Saved</span> : null}
                  {!isPresent && field.default ? (
                    <span className="rounded-full bg-blue-50 px-2 py-1 text-blue-700">Default: {field.default}</span>
                  ) : null}
                </div>
                {canClear ? (
                  <label className="mt-3 flex items-start gap-3 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={Boolean(clearFields[field.key])}
                      onChange={(event) => handleClearChange(field.key, event.target.checked)}
                      className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span>Clear the saved value for this optional field</span>
                  </label>
                ) : null}
              </div>
            )
          })}
        </div>
      </form>
    </Modal>
  )
}
