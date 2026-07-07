import { useState } from 'react'
import { KeyRound } from 'lucide-react'

import { HttpError } from '../../api/http'
import type { SecretDTO, CreateSecretPayload, UpdateSecretPayload } from '../../api/secrets'
import { CheckboxField, FormField, SelectInput, TextareaInput, TextInput } from '../common/FormControls'
import { ModalForm } from '../common/ModalForm'

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

  return (
    <ModalForm
      id="secret-form"
      title={isEdit ? 'Edit Secret' : 'Add Secret'}
      subtitle={isEdit ? `Update "${editSecret?.name}"` : 'Create a new encrypted secret'}
      onClose={onClose}
      onSubmit={handleSubmit}
      widthClass="sm:max-w-lg"
      icon={KeyRound}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-600"
      submitLabel={isEdit ? 'Update Secret' : 'Create Secret'}
      submittingLabel="Saving…"
      submitting={busy}
      errorMessages={allErrors}
      autoComplete="off"
    >
        <FormField id="secret-name" label="Name">
          <TextInput
            id="secret-name"
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. API Key, Database Password"
          />
        </FormField>

        <FormField id="secret-type" label="Type">
          <SelectInput
            id="secret-type"
            value={secretType}
            onChange={(e) => setSecretType(e.target.value as 'credential' | 'env_var')}
          >
            <option value="credential">Credential (domain-scoped)</option>
            <option value="env_var">Environment Variable (sandbox)</option>
          </SelectInput>
        </FormField>

        {secretType === 'credential' && (
          <FormField id="secret-domain" label="Domain Pattern">
            <TextInput
              id="secret-domain"
              type="text"
              required
              value={domainPattern}
              onChange={(e) => setDomainPattern(e.target.value)}
              placeholder="e.g. https://example.com, *.google.com"
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
            />
          </FormField>
        )}

        <FormField
          id="secret-value"
          label={<>Value {isEdit && <span className="text-slate-400">(leave blank to keep current)</span>}</>}
        >
          <TextInput
            id="secret-value"
            type="password"
            required={!isEdit}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={isEdit ? 'Leave blank to keep current value' : 'Enter secret value'}
            autoComplete="new-password"
          />
        </FormField>

        <FormField id="secret-desc" label={<>Description <span className="text-slate-400">(optional)</span></>}>
          <TextareaInput
            id="secret-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            placeholder="What is this secret used for?"
          />
        </FormField>

        {showVisibilityToggle && !isEdit && (
          <CheckboxField
            id="secret-global"
            checked={isGlobal}
            onChange={(e) => setIsGlobal(e.target.checked)}
            label="Global secret"
            helpText="Share this secret across all your agents"
          />
        )}
    </ModalForm>
  )
}
