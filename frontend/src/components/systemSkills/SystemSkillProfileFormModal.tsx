import { useState } from 'react'
import { ExternalLink, KeyRound } from 'lucide-react'

import { HttpError } from '../../api/http'
import type {
  CreateSystemSkillProfilePayload,
  SystemSkillDefinitionDTO,
  SystemSkillProfileDTO,
  UpdateSystemSkillProfilePayload,
} from '../../api/systemSkillProfiles'
import { Modal } from '../common/Modal'


type SystemSkillProfileFormModalProps = {
  definition: SystemSkillDefinitionDTO
  editProfile?: SystemSkillProfileDTO | null
  onClose: () => void
  onSubmit: (data: CreateSystemSkillProfilePayload | UpdateSystemSkillProfilePayload) => Promise<void>
}


export function SystemSkillProfileFormModal({
  definition,
  editProfile = null,
  onClose,
  onSubmit,
}: SystemSkillProfileFormModalProps) {
  const isEdit = editProfile !== null
  const [profileKey, setProfileKey] = useState(editProfile?.profile_key ?? '')
  const [label, setLabel] = useState(editProfile?.label ?? '')
  const [makeDefault, setMakeDefault] = useState(editProfile?.is_default ?? false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string[]>>({})
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({})
  const [clearFields, setClearFields] = useState<Record<string, boolean>>({})
  const isBootstrapSetup = isEdit && !editProfile.complete && editProfile.present_keys.length === 0

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

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setErrors({})

    const values: Record<string, string | null> = {}
    for (const field of definition.fields) {
      const nextValue = (fieldValues[field.key] ?? '').trim()
      if (isEdit) {
        if (clearFields[field.key]) {
          values[field.key] = null
        } else if (nextValue) {
          values[field.key] = nextValue
        }
      } else if (nextValue) {
        values[field.key] = nextValue
      }
    }

    const payload: CreateSystemSkillProfilePayload | UpdateSystemSkillProfilePayload = isEdit
      ? {
          label: label.trim(),
          ...(values && Object.keys(values).length > 0 ? { values } : {}),
        }
      : {
          profile_key: profileKey.trim(),
          label: label.trim(),
          is_default: makeDefault,
          values,
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
        form="system-skill-profile-form"
        className="inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:ml-3 sm:w-auto sm:text-sm disabled:opacity-60"
        disabled={busy}
      >
        {busy ? 'Saving\u2026' : isBootstrapSetup ? 'Save Credentials' : isEdit ? 'Update Profile' : 'Create Profile'}
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
      title={isBootstrapSetup ? 'Complete Setup' : isEdit ? 'Edit Profile' : 'Add Profile'}
      subtitle={
        isBootstrapSetup
          ? `Add the required credentials for ${definition.name}`
          : isEdit
            ? `Update ${editProfile.label}`
            : `Create a ${definition.name} profile`
      }
      onClose={onClose}
      footer={footer}
      widthClass="sm:max-w-2xl"
      icon={KeyRound}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
    >
      <form id="system-skill-profile-form" onSubmit={handleSubmit} className="space-y-4" autoComplete="off">
        {allErrors.length > 0 && (
          <div className="rounded-md bg-red-50 p-3">
            {allErrors.map((message, index) => (
              <p key={index} className="text-sm text-red-700">
                {message}
              </p>
            ))}
          </div>
        )}

        {!isEdit && (
          <div>
            <label htmlFor="profile-key" className="block text-sm font-medium text-slate-700">
              Profile Key
            </label>
            <input
              id="profile-key"
              type="text"
              required
              value={profileKey}
              onChange={(event) => setProfileKey(event.target.value)}
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
              placeholder="e.g. default, client_a, client_b"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
            />
            <p className="mt-1 text-xs text-slate-500">Lowercase letters, numbers, underscores, and hyphens only.</p>
          </div>
        )}

        <div>
          <label htmlFor="profile-label" className="block text-sm font-medium text-slate-700">
            Label
          </label>
          <input
            id="profile-label"
            type="text"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
            placeholder="Human-friendly profile name"
          />
        </div>

        {!isEdit && (
          <label className="flex items-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
            <input
              type="checkbox"
              checked={makeDefault}
              onChange={(event) => setMakeDefault(event.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
            />
            <span className="text-sm text-slate-700">
              <span className="font-medium">Use as default profile</span>
              <span className="mt-1 block text-xs text-slate-500">
                This profile will be used automatically when an agent does not pass a profile key.
              </span>
            </span>
          </label>
        )}

        <div className="space-y-4">
          {definition.fields.map((field) => {
            const isPresent = Boolean(editProfile?.present_keys.includes(field.key))
            const canClear = isEdit && isPresent && !field.required
            return (
              <div key={field.key} className="rounded-lg border border-slate-200 p-4">
                <label htmlFor={`field-${field.key}`} className="block text-sm font-medium text-slate-700">
                  {field.name}
                  {field.required ? ' *' : ''}
                </label>
                {field.description && <p className="mt-1 text-xs text-slate-500">{field.description}</p>}
                {field.how_to_get && (
                  <div className="mt-3 rounded-lg bg-blue-50 px-3 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-blue-700">How To Get This</p>
                    <p className="mt-1 text-sm text-blue-900">{field.how_to_get}</p>
                    {field.docs.length > 0 && (
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
                            <ExternalLink className="h-3 w-3" />
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                <input
                  id={`field-${field.key}`}
                  type="password"
                  value={fieldValues[field.key] ?? ''}
                  onChange={(event) => handleFieldChange(field.key, event.target.value)}
                  required={!isEdit && field.required && !field.default}
                  className="mt-2 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500"
                  placeholder={
                    isEdit
                      ? isPresent
                        ? 'Leave blank to keep the saved value'
                        : field.default
                          ? `Leave blank to use default (${field.default})`
                          : 'Enter a replacement value'
                      : field.default
                        ? `Optional, defaults to ${field.default}`
                        : 'Enter a value'
                  }
                  autoComplete="new-password"
                  disabled={Boolean(clearFields[field.key])}
                />
                <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                  {isPresent && <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700">Saved</span>}
                  {!isPresent && field.default && (
                    <span className="rounded-full bg-blue-50 px-2 py-1 text-blue-700">Default: {field.default}</span>
                  )}
                </div>
                {canClear && (
                  <label className="mt-3 flex items-start gap-3 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={Boolean(clearFields[field.key])}
                      onChange={(event) => handleClearChange(field.key, event.target.checked)}
                      className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span>Clear the saved value for this optional field</span>
                  </label>
                )}
              </div>
            )
          })}
        </div>
      </form>
    </Modal>
  )
}
