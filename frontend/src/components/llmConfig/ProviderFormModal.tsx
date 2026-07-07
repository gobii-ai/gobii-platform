import { PlugZap } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import * as llmApi from '../../api/llmConfig'
import { CheckboxField, FormField, SelectInput, TextInput } from '../common/FormControls'
import { ModalForm } from '../common/ModalForm'

const backendOptions: Array<{ value: llmApi.ProviderBrowserBackend; label: string }> = [
  { value: 'OPENAI', label: 'OpenAI' },
  { value: 'ANTHROPIC', label: 'Anthropic' },
  { value: 'GOOGLE', label: 'Google' },
  { value: 'OPENAI_COMPAT', label: 'OpenAI-compatible' },
]

type ProviderFormModalProps = {
  onCreate: (payload: llmApi.ProviderCreatePayload) => Promise<void>
  onClose: () => void
  busy?: boolean
}

export function ProviderFormModal({ onCreate, onClose, busy }: ProviderFormModalProps) {
  const [displayName, setDisplayName] = useState('')
  const [key, setKey] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [envVarName, setEnvVarName] = useState('')
  const [modelPrefix, setModelPrefix] = useState('')
  const [browserBackend, setBrowserBackend] = useState<llmApi.ProviderBrowserBackend>('OPENAI')
  const [supportsSafetyIdentifier, setSupportsSafetyIdentifier] = useState(false)
  const [vertexProject, setVertexProject] = useState('')
  const [vertexLocation, setVertexLocation] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const isSubmitting = Boolean(busy || submitting)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSubmitting(true)
    try {
      await onCreate({
        display_name: displayName.trim(),
        key: key.trim(),
        api_key: apiKey.trim(),
        env_var_name: envVarName.trim(),
        model_prefix: modelPrefix.trim(),
        browser_backend: browserBackend,
        supports_safety_identifier: supportsSafetyIdentifier,
        vertex_project: vertexProject.trim(),
        vertex_location: vertexLocation.trim(),
        enabled,
      })
      onClose()
    } catch {
      // The shared feedback dock shows API errors; keep the dialog open for correction.
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalForm
      id="llm-provider-create-form"
      title="Add provider"
      subtitle="Configure provider credentials and runtime defaults."
      icon={PlugZap}
      onClose={onClose}
      onSubmit={handleSubmit}
      submitLabel="Add provider"
      submittingLabel="Adding provider..."
      submitting={isSubmitting}
      submitDisabled={!displayName.trim() || !key.trim()}
      widthClass="sm:max-w-2xl"
      autoComplete="off"
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormField id="llm-provider-display-name" label="Display name">
          <TextInput
            id="llm-provider-display-name"
            value={displayName}
            onChange={(event) => setDisplayName(event.currentTarget.value)}
            placeholder="OpenRouter"
            autoFocus
          />
        </FormField>
        <FormField id="llm-provider-key" label="Provider key">
          <TextInput
            id="llm-provider-key"
            type="text"
            value={key}
            onChange={(event) => setKey(event.currentTarget.value.toLowerCase().replace(/[^a-z0-9_-]/g, ''))}
            placeholder="openrouter"
          />
        </FormField>
        <FormField id="llm-provider-api-key" label="Admin API key" className="sm:col-span-2">
          <TextInput
            id="llm-provider-api-key"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.currentTarget.value)}
            placeholder="Optional"
            autoComplete="new-password"
          />
        </FormField>
        <FormField id="llm-provider-env-var" label="Environment fallback">
          <TextInput
            id="llm-provider-env-var"
            value={envVarName}
            onChange={(event) => setEnvVarName(event.currentTarget.value)}
            placeholder="OPENROUTER_API_KEY"
          />
        </FormField>
        <FormField id="llm-provider-model-prefix" label="Model prefix">
          <TextInput
            id="llm-provider-model-prefix"
            value={modelPrefix}
            onChange={(event) => setModelPrefix(event.currentTarget.value)}
            placeholder="openrouter/"
          />
        </FormField>
        <FormField id="llm-provider-browser-backend" label="Browser backend">
          <SelectInput
            id="llm-provider-browser-backend"
            value={browserBackend}
            onChange={(event) => setBrowserBackend(event.currentTarget.value as llmApi.ProviderBrowserBackend)}
          >
            {backendOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </SelectInput>
        </FormField>
        <FormField id="llm-provider-vertex-project" label="Vertex project">
          <TextInput
            id="llm-provider-vertex-project"
            value={vertexProject}
            onChange={(event) => setVertexProject(event.currentTarget.value)}
            placeholder="Optional"
          />
        </FormField>
        <FormField id="llm-provider-vertex-location" label="Vertex location">
          <TextInput
            id="llm-provider-vertex-location"
            value={vertexLocation}
            onChange={(event) => setVertexLocation(event.currentTarget.value)}
            placeholder="us-east4"
          />
        </FormField>
      </div>
      <div className="flex flex-wrap gap-4 text-sm text-slate-700">
        <CheckboxField
          id="llm-provider-enabled"
          checked={enabled}
          onChange={(event) => setEnabled(event.currentTarget.checked)}
          label="Enabled"
          containerClassName="inline-flex items-center gap-2"
        />
        <CheckboxField
          id="llm-provider-supports-safety-identifier"
          checked={supportsSafetyIdentifier}
          onChange={(event) => setSupportsSafetyIdentifier(event.currentTarget.checked)}
          label="Supports safety identifiers"
          containerClassName="inline-flex items-center gap-2"
        />
      </div>
    </ModalForm>
  )
}
