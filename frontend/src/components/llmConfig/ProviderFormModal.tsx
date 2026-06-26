import { PlugZap } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import * as llmApi from '../../api/llmConfig'
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
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Display name</span>
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.currentTarget.value)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            placeholder="OpenRouter"
            autoFocus
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Provider key</span>
          <input
            value={key}
            onChange={(event) => setKey(event.currentTarget.value)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            placeholder="openrouter"
          />
        </label>
        <label className="block sm:col-span-2">
          <span className="text-sm font-medium text-slate-700">Admin API key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.currentTarget.value)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            placeholder="Optional"
            autoComplete="new-password"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Environment fallback</span>
          <input
            value={envVarName}
            onChange={(event) => setEnvVarName(event.currentTarget.value)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            placeholder="OPENROUTER_API_KEY"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Model prefix</span>
          <input
            value={modelPrefix}
            onChange={(event) => setModelPrefix(event.currentTarget.value)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            placeholder="openrouter/"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Browser backend</span>
          <select
            value={browserBackend}
            onChange={(event) => setBrowserBackend(event.currentTarget.value as llmApi.ProviderBrowserBackend)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
          >
            {backendOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Vertex project</span>
          <input
            value={vertexProject}
            onChange={(event) => setVertexProject(event.currentTarget.value)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            placeholder="Optional"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Vertex location</span>
          <input
            value={vertexLocation}
            onChange={(event) => setVertexLocation(event.currentTarget.value)}
            className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            placeholder="us-east4"
          />
        </label>
      </div>
      <div className="flex flex-wrap gap-4 text-sm text-slate-700">
        <label className="inline-flex items-center gap-2">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.currentTarget.checked)}
            className="rounded border-slate-300 text-blue-600 shadow-sm"
          />
          Enabled
        </label>
        <label className="inline-flex items-center gap-2">
          <input
            type="checkbox"
            checked={supportsSafetyIdentifier}
            onChange={(event) => setSupportsSafetyIdentifier(event.currentTarget.checked)}
            className="rounded border-slate-300 text-blue-600 shadow-sm"
          />
          Supports safety identifiers
        </label>
      </div>
    </ModalForm>
  )
}
