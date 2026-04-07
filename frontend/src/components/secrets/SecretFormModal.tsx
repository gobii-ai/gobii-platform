import { useState } from 'react'
import { KeyRound } from 'lucide-react'

import { HttpError } from '../../api/http'
import type { SecretDTO, CreateSecretPayload, UpdateSecretPayload } from '../../api/secrets'
import { Modal } from '../common/Modal'

type SecretFormModalProps = {
  /** Existing secret to edit, or null for create mode. */
  editSecret?: SecretDTO | null
  /** Whether to show the visibility (agent/global) toggle. */
  showVisibilityToggle?: boolean
  onClose: () => void
  onSubmit: (data: CreateSecretPayload | UpdateSecretPayload) => Promise<void>
}

export function SecretFormModal({
  editSecret = null,
  showVisibilityToggle = false,
  onClose,
  onSubmit,
}: SecretFormModalProps) {
  const isEdit = editSecret !== null

  const [name, setName] = useState(editSecret?.name ?? '')
  const [secretType, setSecretType] = useState<'credential' | 'env_var'>(editSecret?.secret_type ?? 'credential')
  const [domainPattern, setDomainPattern] = useState(editSecret?.domain_pattern ?? '')
  const [value, setValue] = useState('')
  const [description, setDescription] = useState(editSecret?.description ?? '')
  const [isGlobal, setIsGlobal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string[]>>({})

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setErrors({})

    const payload: CreateSecretPayload | UpdateSecretPayload = isEdit
      ? {
          name: name.trim(),
          secret_type: secretType,
          domain_pattern: secretType === 'credential' ? domainPattern.trim() : undefined,
          description: description.trim(),
          ...(value ? { value } : {}),
        }
      : {
          name: name.trim(),
          secret_type: secretType,
          domain_pattern: secretType === 'credential' ? domainPattern.trim() : undefined,
          value,
          description: description.trim(),
          ...(showVisibilityToggle ? { is_global: isGlobal } : {}),
        }

    try {
      await onSubmit(payload)
      onClose()
    } catch (error) {
      if (error instanceof HttpError && typeof error.body === 'object' && error.body) {
        const body = error.body as Record<string, unknown>
        if (body.errors && typeof body.errors === 'object') {
          const parsed: Record<string, string[]> = {}
          for (const [field, msgs] of Object.entries(body.errors as Record<string, unknown>)) {
            parsed[field] = Array.isArray(msgs) ? msgs.map(String) : [String(msgs)]
          }
          setErrors(parsed)
        } else {
          setErrors({ __all__: [body.error ? String(body.error) : 'An error occurred.'] })
        }
      } else {
        setErrors({ __all__: [error instanceof Error ? error.message : 'An error occurred.'] })
      }
    } finally {
      setBusy(false)
    }
  }

  const allErrors = Object.values(errors).flat()

  const footer = (
    <>
      <button
        type="submit"
        form="secret-form"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        disabled={busy}
      >
        {busy ? 'Saving\u2026' : isEdit ? 'Update Secret' : 'Create Secret'}
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
      title={isEdit ? 'Edit Secret' : 'Add Secret'}
      subtitle={isEdit ? `Update "${editSecret?.name}"` : 'Create a new encrypted secret'}
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-lg"
      icon={KeyRound}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
    >
      <form id="secret-form" onSubmit={handleSubmit} className="space-y-4" autoComplete="off">
        {allErrors.length > 0 && (
          <div className="rounded-md bg-red-50 p-3">
            {allErrors.map((msg, i) => (
              <p key={i} className="text-sm text-red-700">{msg}</p>
            ))}
          </div>
        )}

        <div>
          <label htmlFor="secret-name" className="block text-sm font-medium text-slate-700">
            Name
          </label>
          <input
            id="secret-name"
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            placeholder="e.g. API Key, Database Password"
          />
        </div>

        <div>
          <label htmlFor="secret-type" className="block text-sm font-medium text-slate-700">
            Type
          </label>
          <select
            id="secret-type"
            value={secretType}
            onChange={(e) => setSecretType(e.target.value as 'credential' | 'env_var')}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
          >
            <option value="credential">Credential (domain-scoped)</option>
            <option value="env_var">Environment Variable (sandbox)</option>
          </select>
        </div>

        {secretType === 'credential' && (
          <div>
            <label htmlFor="secret-domain" className="block text-sm font-medium text-slate-700">
              Domain Pattern
            </label>
            <input
              id="secret-domain"
              type="text"
              required
              value={domainPattern}
              onChange={(e) => setDomainPattern(e.target.value)}
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
              placeholder="e.g. https://example.com, *.google.com"
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
            />
          </div>
        )}

        <div>
          <label htmlFor="secret-value" className="block text-sm font-medium text-slate-700">
            Value {isEdit && <span className="text-slate-400">(leave blank to keep current)</span>}
          </label>
          <input
            id="secret-value"
            type="password"
            required={!isEdit}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            placeholder={isEdit ? 'Leave blank to keep current value' : 'Enter secret value'}
            autoComplete="new-password"
          />
        </div>

        <div>
          <label htmlFor="secret-desc" className="block text-sm font-medium text-slate-700">
            Description <span className="text-slate-400">(optional)</span>
          </label>
          <textarea
            id="secret-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            rows={2}
            placeholder="What is this secret used for?"
          />
        </div>

        {showVisibilityToggle && !isEdit && (
          <div className="flex items-center gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
            <input
              id="secret-global"
              type="checkbox"
              checked={isGlobal}
              onChange={(e) => setIsGlobal(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
            />
            <label htmlFor="secret-global" className="text-sm text-slate-700">
              <span className="font-medium">Global secret</span>
              <span className="block text-xs text-slate-500">Share this secret across all your agents</span>
            </label>
          </div>
        )}
      </form>
    </Modal>
  )
}
