import { ChevronDown, Copy, KeyRound, Loader2, Plus, ShieldCheck, Trash2, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import {
  Button as AriaButton,
  Dialog,
  DialogTrigger,
  ListBox,
  ListBoxItem,
  Popover,
  type Key,
  type Selection,
} from 'react-aria-components'

import * as llmApi from '../../api/llmConfig'
import {
  actionKey,
  addEndpointOptions,
  button,
  type EndpointFormValues,
  type EndpointTestStatus,
  type ProviderCardData,
  type ProviderEndpointCard,
  reasoningEffortOptions,
} from './shared'

export type ProviderCardHandlers = {
  onRotateKey: (provider: ProviderCardData) => Promise<void>
  onToggleEnabled: (provider: ProviderCardData, enabled: boolean) => Promise<void>
  onAddEndpoint: (provider: ProviderCardData, type: llmApi.ProviderEndpoint['type'], values: EndpointFormValues & { key: string }) => Promise<void>
  onSaveEndpoint: (endpoint: ProviderEndpointCard, values: EndpointFormValues) => Promise<void>
  onDeleteEndpoint: (endpoint: ProviderEndpointCard) => Promise<void>
  onClearKey: (provider: ProviderCardData) => Promise<void>
  onTestEndpoint: (endpoint: ProviderEndpointCard) => Promise<void>
}

export function ProviderCard({ provider, handlers, isBusy, testStatuses, showModal, closeModal }: { provider: ProviderCardData; handlers: ProviderCardHandlers; isBusy: (key: string) => boolean; testStatuses: Record<string, EndpointTestStatus | undefined>; showModal: (renderer: (onClose: () => void) => ReactNode) => void; closeModal: () => void }) {
  const [activeTab, setActiveTab] = useState<'endpoints' | 'settings'>('endpoints')
  const [editingEndpointId, setEditingEndpointId] = useState<string | null>(null)
  const rotateBusy = isBusy(actionKey('provider', provider.id, 'rotate'))
  const clearBusy = isBusy(actionKey('provider', provider.id, 'clear'))
  const toggleBusy = isBusy(actionKey('provider', provider.id, 'toggle'))
  const creatingEndpoint = isBusy(actionKey('provider', provider.id, 'create-endpoint'))
  const [isAddMenuOpen, setIsAddMenuOpen] = useState(false)
  const [selectedAddEndpointKeys, setSelectedAddEndpointKeys] = useState<Set<Key>>(new Set())

  const handleAddMenuOpenChange = (open: boolean) => {
    setIsAddMenuOpen(open)
    if (!open) {
      setSelectedAddEndpointKeys(new Set())
    }
  }

  const openAddEndpointModal = (type: llmApi.ProviderEndpoint['type'], sourceEndpoint?: ProviderEndpointCard) => {
    showModal((onClose) => createPortal(
      <ProviderEndpointModal
        mode="create"
        providerName={provider.name}
        type={type}
        sourceEndpoint={sourceEndpoint}
        busy={creatingEndpoint}
        onClose={onClose}
        onSubmit={async (values) => {
          try {
            await handlers.onAddEndpoint(provider, type, values as EndpointFormValues & { key: string })
            onClose()
          } catch {
            // feedback already shown
          }
        }}
      />,
      document.body,
    ))
  }

  const handleAddEndpointSelection = (keys: Selection) => {
    if (keys === 'all') return
    const selection = keys as Set<Key>
    const selectedKey = selection.values().next().value
    if (!selectedKey) return
    setSelectedAddEndpointKeys(new Set())
    setIsAddMenuOpen(false)
    openAddEndpointModal(String(selectedKey) as llmApi.ProviderEndpoint['type'])
  }

  const openEndpointEditor = (endpoint: ProviderEndpointCard) => {
    const isEditing = editingEndpointId === endpoint.id
    if (isEditing) {
      setEditingEndpointId(null)
      closeModal()
      return
    }
    setEditingEndpointId(endpoint.id)
    showModal((onClose) =>
      createPortal(
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-900/60">
          <div className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-2xl">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold">
                {endpoint.type === 'persistent'
                  ? 'Edit persistent endpoint'
                  : endpoint.type === 'browser'
                    ? 'Edit browser endpoint'
                    : endpoint.type === 'file_handler'
                      ? 'Edit file handler endpoint'
                      : endpoint.type === 'image_generation'
                        ? 'Edit image generation endpoint'
                        : endpoint.type === 'video_generation'
                          ? 'Edit video generation endpoint'
                        : 'Edit embedding endpoint'}
              </h3>
              <button
                onClick={() => {
                  setEditingEndpointId(null)
                  onClose()
                }}
                className={button.icon}
              >
                <X className="size-5" />
              </button>
            </div>
            <p className="text-sm text-slate-500 mt-1">{provider.name}</p>
            <div className="mt-4">
              <ProviderEndpointForm
                mode="edit"
                endpoint={endpoint}
                type={endpoint.type}
                saving={isBusy(actionKey('endpoint', endpoint.id, 'update'))}
                onCancel={() => {
                  setEditingEndpointId(null)
                  onClose()
                }}
                onSave={async (values) => {
                  try {
                    await handlers.onSaveEndpoint(endpoint, values)
                    setEditingEndpointId(null)
                    onClose()
                  } catch {
                    // feedback already shown
                  }
                }}
              />
            </div>
          </div>
        </div>,
        document.body,
      ),
    )
  }

  return (
    <article className="rounded-2xl border border-slate-200/80 bg-white">
      <div className="flex items-center justify-between p-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900/90">{provider.name}</h3>
          <p className="text-xs text-slate-500">{provider.endpoints.length} endpoints</p>
        </div>
        <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${provider.enabled ? 'bg-emerald-50/80 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
          <ShieldCheck className="size-3.5" /> {provider.status}
        </span>
      </div>
      <div className="border-b border-slate-200/80 px-4">
        <nav className="-mb-px flex space-x-6" aria-label="Tabs">
          <button onClick={() => setActiveTab('endpoints')} className={`whitespace-nowrap border-b-2 py-2 px-1 text-sm font-medium ${activeTab === 'endpoints' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700'}`}>
            Endpoints
          </button>
          <button onClick={() => setActiveTab('settings')} className={`whitespace-nowrap border-b-2 py-2 px-1 text-sm font-medium ${activeTab === 'settings' ? 'border-blue-500 text-blue-600' : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700'}`}>
            Settings
          </button>
        </nav>
      </div>
      <div className="p-4 space-y-4">
        {activeTab === 'endpoints' && (
          <>
            <div className="flex items-center justify-between">
              <p className="text-sm text-slate-600">Manage provider endpoints</p>
              <DialogTrigger isOpen={isAddMenuOpen} onOpenChange={handleAddMenuOpenChange}>
                <AriaButton className={button.secondary} isDisabled={creatingEndpoint}>
                  <Plus className="size-4" /> Add endpoint
                  <ChevronDown className={`size-4 text-slate-400 transition ${isAddMenuOpen ? 'rotate-180' : ''}`} />
                </AriaButton>
                <Popover className="z-50 mt-2 w-56 rounded-xl border border-slate-200 bg-white shadow-xl">
                  <Dialog className="p-2">
                    <ListBox
                      aria-label="Select endpoint type"
                      selectionMode="single"
                      selectedKeys={selectedAddEndpointKeys as unknown as Selection}
                      onSelectionChange={(keys) => handleAddEndpointSelection(keys as Selection)}
                      className="space-y-1 text-sm"
                    >
                      {addEndpointOptions.map((option) => (
                        <ListBoxItem
                          key={option.id}
                          id={option.id}
                          textValue={option.label}
                          className="flex w-full cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-sm text-slate-700 data-[hovered]:bg-blue-50 data-[hovered]:text-blue-700 data-[focused]:bg-blue-50 data-[focused]:text-blue-700 data-[selected]:bg-blue-600 data-[selected]:text-white"
                        >
                          <span className="font-medium">{option.label}</span>
                        </ListBoxItem>
                      ))}
                    </ListBox>
                  </Dialog>
                </Popover>
              </DialogTrigger>
            </div>
            {provider.endpoints.length === 0 && <p className="text-sm text-slate-500">No endpoints linked.</p>}
            <div className="space-y-3">
              {provider.endpoints.map((endpoint) => {
                const isEditing = editingEndpointId === endpoint.id
                const deleteBusy = isBusy(actionKey('endpoint', endpoint.id, 'delete'))
                const testBusy = isBusy(actionKey('endpoint', endpoint.id, 'test'))
                const status = testStatuses[endpoint.id]
                const tone = status?.state === 'success'
                  ? 'text-emerald-600'
                  : status?.state === 'error'
                    ? 'text-rose-600'
                    : 'text-slate-500'
                const isPendingStatus = status?.state === 'pending'
                const headline = status?.state === 'success'
                  ? 'Success:'
                  : status?.state === 'error'
                    ? 'Error:'
                    : 'Testing…'
                return (
                  <div key={endpoint.id} className="rounded-lg border border-slate-200 p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-semibold text-slate-900/90">{endpoint.name}</p>
                        <p className="text-xs text-slate-500 uppercase">{endpoint.type}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <button type="button" className={button.secondary} onClick={() => handlers.onTestEndpoint(endpoint).catch(() => {})} disabled={testBusy}>
                          {testBusy ? <Loader2 className="size-4 animate-spin" /> : 'Test'}
                        </button>
                        <button type="button" className={button.secondary} onClick={() => openAddEndpointModal(endpoint.type, endpoint)} disabled={creatingEndpoint}>
                          {creatingEndpoint ? <Loader2 className="size-4 animate-spin" /> : <Copy className="size-4" />}
                          Clone
                        </button>
                        <button className={button.secondary} onClick={() => openEndpointEditor(endpoint)}>
                          {isEditing ? 'Close' : 'Edit'}
                        </button>
                        <button className={button.iconDanger} onClick={() => handlers.onDeleteEndpoint(endpoint).catch(() => {})} disabled={deleteBusy}>
                          {deleteBusy ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                        </button>
                      </div>
                    </div>
                    {status && (
                      <div className={`mt-3 text-xs ${tone}`}>
                        <p className="font-medium">
                          {isPendingStatus
                            ? status.message
                            : (
                              <>
                                <span>{headline}</span>
                                {status.message ? ` ${status.message}` : ''}
                              </>
                            )}
                        </p>
                        {status.state === 'success' && (
                          <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-slate-500">
                            {status.latencyMs != null && <span>Latency: {status.latencyMs} ms</span>}
                            {status.totalTokens != null && <span>Total tokens: {status.totalTokens}</span>}
                            {status.promptTokens != null && <span>Prompt: {status.promptTokens}</span>}
                            {status.completionTokens != null && <span>Completion: {status.completionTokens}</span>}
                          </div>
                        )}
                        {status.preview ? (
                          <p className="mt-1 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-600">
                            Preview: <span className="font-mono">{status.preview}</span>
                          </p>
                        ) : null}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
        {activeTab === 'settings' && (
          <div className="space-y-4 text-sm text-slate-600">
            <div>
              <p className="font-semibold text-slate-900/90">Provider key</p>
              <p className="text-xs text-slate-500 break-all">{provider.key}</p>
            </div>
            <div>
              <p className="font-semibold text-slate-900/90">Environment fallback</p>
              <p className="text-xs text-slate-500 break-all">{provider.fallback}</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-slate-500 uppercase">Backend</p>
                <p className="font-medium text-slate-900/90">{provider.backend}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase">Model prefix</p>
                <p className="font-medium text-slate-900/90">{provider.modelPrefix || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase">Safety identifiers</p>
                <p className="font-medium text-slate-900/90">{provider.supportsSafety ? 'Supported' : 'Disabled'}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase">Vertex project</p>
                <p className="font-medium text-slate-900/90">{provider.vertexProject || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase">Vertex location</p>
                <p className="font-medium text-slate-900/90">{provider.vertexLocation || '—'}</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className={button.primary} onClick={() => handlers.onRotateKey(provider).catch(() => {})} disabled={rotateBusy}>
                {rotateBusy ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />} Rotate key
              </button>
              <button className={button.secondary} onClick={() => handlers.onClearKey(provider).catch(() => {})} disabled={clearBusy}>
                {clearBusy ? <Loader2 className="size-4 animate-spin" /> : null} Clear key
              </button>
              <button className={button.muted} onClick={() => handlers.onToggleEnabled(provider, !provider.enabled).catch(() => {})} disabled={toggleBusy}>
                {toggleBusy ? 'Working…' : provider.enabled ? 'Disable provider' : 'Enable provider'}
              </button>
            </div>
          </div>
        )}
      </div>
    </article>
  )
}

const endpointTypeLabels: Record<llmApi.ProviderEndpoint['type'], string> = {
  persistent: 'persistent',
  browser: 'browser',
  embedding: 'embedding',
  file_handler: 'file handler',
  image_generation: 'image generation',
  video_generation: 'video generation',
}

type ProviderEndpointFormMode = 'create' | 'edit'

type ProviderEndpointFormProps = {
  mode: ProviderEndpointFormMode
  type: llmApi.ProviderEndpoint['type']
  endpoint?: ProviderEndpointCard
  saving?: boolean
  submitLabel?: string
  onSave?: (values: EndpointFormValues) => Promise<void> | void
  onSubmit?: (values: EndpointFormValues & { key?: string }) => Promise<void> | void
  onCancel?: () => void
  onClose?: () => void
}

function ProviderEndpointForm({
  mode,
  type,
  endpoint,
  onSave,
  onSubmit,
  onCancel,
  onClose,
  saving,
  submitLabel,
}: ProviderEndpointFormProps) {
  const isCreate = mode === 'create'
  const [key, setKey] = useState(isCreate && endpoint?.key ? `${endpoint.key}-copy` : '')
  const [model, setModel] = useState(endpoint?.name ?? '')
  const [pricingModel, setPricingModel] = useState(endpoint?.litellm_pricing_model ?? '')
  const [temperature, setTemperature] = useState(endpoint?.temperature?.toString() ?? '')
  const [supportsTemperature, setSupportsTemperature] = useState(
    endpoint?.supports_temperature ?? true,
  )
  const [apiBase, setApiBase] = useState(endpoint?.api_base || endpoint?.browser_base_url || '')
  const [maxTokens, setMaxTokens] = useState(endpoint?.max_output_tokens?.toString() ?? '')
  const [maxInputTokens, setMaxInputTokens] = useState(endpoint?.max_input_tokens?.toString() ?? '')
  const [supportsVision, setSupportsVision] = useState(Boolean(endpoint?.supports_vision))
  const [supportsImageToImage, setSupportsImageToImage] = useState(Boolean(endpoint?.supports_image_to_image))
  const [supportsImageToVideo, setSupportsImageToVideo] = useState(Boolean(endpoint?.supports_image_to_video))
  const [supportsToolChoice, setSupportsToolChoice] = useState(endpoint?.supports_tool_choice ?? true)
  const [parallelTools, setParallelTools] = useState(endpoint?.use_parallel_tool_calls ?? true)
  const [allowImpliedSend, setAllowImpliedSend] = useState(endpoint?.allow_implied_send ?? true)
  const [supportsReasoning, setSupportsReasoning] = useState(Boolean(endpoint?.supports_reasoning))
  const [reasoningEffort, setReasoningEffort] = useState(endpoint?.reasoning_effort ?? '')
  const [openrouterPreset, setOpenrouterPreset] = useState(endpoint?.openrouter_preset ?? '')
  const [lowLatency, setLowLatency] = useState(Boolean(endpoint?.low_latency))
  const [submitting, setSubmitting] = useState(false)
  const isSubmitting = Boolean(saving || submitting)

  const handleSubmit = async () => {
    const values: EndpointFormValues = {
      model,
      litellm_pricing_model: pricingModel,
      temperature,
      api_base: apiBase,
      browser_base_url: apiBase,
      max_output_tokens: maxTokens,
      max_input_tokens: maxInputTokens,
      supportsTemperature,
      supportsToolChoice: supportsToolChoice,
      useParallelToolCalls: parallelTools,
      allowImpliedSend,
      supportsVision: supportsVision,
      supportsImageToImage,
      supportsImageToVideo,
      supportsReasoning,
      reasoningEffort,
      openrouterPreset,
      lowLatency,
    }
    if (isCreate) {
      setSubmitting(true)
      try {
        await onSubmit?.({ ...values, key })
      } finally {
        setSubmitting(false)
      }
      return
    }
    await onSave?.(values)
  }

  const isBrowser = type === 'browser'
  const isEmbedding = type === 'embedding'
  const isFileHandler = type === 'file_handler'
  const isImageGeneration = type === 'image_generation'
  const isVideoGeneration = type === 'video_generation'
  const isPersistent = type === 'persistent'
  const isLiteLLMEndpoint = !isBrowser
  const isMediaGeneration = isImageGeneration || isVideoGeneration
  const isToolingEndpoint = !isEmbedding && !isFileHandler && !isMediaGeneration
  const showTemperatureOverride = isCreate ? (isPersistent || isEmbedding) : (!isBrowser && !isMediaGeneration)
  const showReasoningControls = isCreate ? isPersistent : (!isBrowser && isToolingEndpoint)

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {isCreate && (
          <div>
            <label className="text-xs text-slate-500">Endpoint key</label>
            <input value={key} onChange={(event) => setKey(event.target.value)} className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
          </div>
        )}
        <div>
          <label className="text-xs text-slate-500">Model identifier</label>
          <input value={model} onChange={(event) => setModel(event.target.value)} className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
        </div>
        {isLiteLLMEndpoint && (
          <div>
            <label className="text-xs text-slate-500">LiteLLM pricing model override</label>
            <input
              value={pricingModel}
              onChange={(event) => setPricingModel(event.target.value)}
              placeholder="Optional"
              className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
        )}
        {showTemperatureOverride && (
          <div>
            <label className="text-xs text-slate-500">Temperature override</label>
            <input type="number" value={temperature} onChange={(event) => setTemperature(event.target.value)} placeholder="auto" disabled={!supportsTemperature} className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm disabled:bg-slate-50 disabled:text-slate-400" />
          </div>
        )}
        <div className={isCreate ? 'md:col-span-2' : undefined}>
          <label className="text-xs text-slate-500">API base URL</label>
          <input value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder="https://api.example.com/v1" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
        </div>
        {isPersistent && (
          <div className="md:col-span-2">
            <label className="text-xs text-slate-500">OpenRouter preset</label>
            <input
              value={openrouterPreset}
              onChange={(event) => setOpenrouterPreset(event.target.value)}
              placeholder="Optional (OpenRouter only)"
              className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
            />
          </div>
        )}
        {isBrowser && (
          <div>
            <label className="text-xs text-slate-500">Max output tokens</label>
            <input type="number" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} placeholder="Default" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
          </div>
        )}
        {isPersistent && (
          <div>
            <label className="text-xs text-slate-500">Max input tokens</label>
            <input type="number" value={maxInputTokens} onChange={(event) => setMaxInputTokens(event.target.value)} placeholder="Automatic" className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm" />
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-4 text-sm">
        {!isMediaGeneration && (
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" checked={supportsTemperature} onChange={(event) => setSupportsTemperature(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
            Supports temperature
          </label>
        )}
        {!isMediaGeneration && (
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" checked={supportsVision} onChange={(event) => setSupportsVision(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
            Vision
          </label>
        )}
        {isImageGeneration && (
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={supportsImageToImage}
              onChange={(event) => setSupportsImageToImage(event.target.checked)}
              className="rounded border-slate-300 text-blue-600 shadow-sm"
            />
            Supports image-to-image
          </label>
        )}
        {isVideoGeneration && (
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={supportsImageToVideo}
              onChange={(event) => setSupportsImageToVideo(event.target.checked)}
              className="rounded border-slate-300 text-blue-600 shadow-sm"
            />
            Supports image-to-video
          </label>
        )}
        {showReasoningControls && (
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={supportsReasoning}
              onChange={(event) => {
                setSupportsReasoning(event.target.checked)
                if (!event.target.checked) setReasoningEffort('')
              }}
              className="rounded border-slate-300 text-blue-600 shadow-sm"
            />
            Reasoning
          </label>
        )}
        {isToolingEndpoint && (
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" checked={supportsToolChoice} onChange={(event) => setSupportsToolChoice(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
            Tool choice
          </label>
        )}
        {isToolingEndpoint && (
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" checked={parallelTools} onChange={(event) => setParallelTools(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
            Parallel calls
          </label>
        )}
        {isPersistent && (
          <label className="inline-flex items-center gap-2">
            <input type="checkbox" checked={allowImpliedSend} onChange={(event) => setAllowImpliedSend(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
            Implied send
          </label>
        )}
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" checked={lowLatency} onChange={(event) => setLowLatency(event.target.checked)} className="rounded border-slate-300 text-blue-600 shadow-sm" />
          Low latency
        </label>
      </div>
      {showReasoningControls && (
        <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600">
          <span className="font-semibold text-slate-700">Default reasoning effort</span>
          <select
            value={reasoningEffort}
            onChange={(event) => setReasoningEffort(event.target.value)}
            disabled={!supportsReasoning}
            className="rounded-lg border border-slate-300 py-1.5 text-xs shadow-sm focus:border-blue-500 focus:ring-blue-500 disabled:bg-slate-50 disabled:text-slate-400"
          >
            {reasoningEffortOptions.map((option) => (
              <option key={option.value || 'default'} value={option.value}>{option.label}</option>
            ))}
          </select>
          {isCreate ? <span className="text-slate-400">Optional override when reasoning is enabled.</span> : null}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <button className={button.secondary} onClick={onCancel || onClose} disabled={isSubmitting}>Cancel</button>
        <button className={button.primary} onClick={handleSubmit} disabled={isCreate ? (!key || !model || isSubmitting) : isSubmitting}>
          {isSubmitting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : isCreate ? <Plus className="size-4" /> : null}
          {isCreate ? (submitLabel ?? 'Add endpoint') : 'Save changes'}
        </button>
      </div>
    </div>
  )
}

type ProviderEndpointModalProps = {
  mode: 'create'
  providerName: string
  type: llmApi.ProviderEndpoint['type']
  sourceEndpoint?: ProviderEndpointCard
  busy?: boolean
  onSubmit: (values: EndpointFormValues & { key?: string }) => Promise<void> | void
  onClose: () => void
}

function ProviderEndpointModal({ providerName, type, sourceEndpoint, onSubmit, onClose, busy }: ProviderEndpointModalProps) {
  const title = sourceEndpoint ? `Clone ${endpointTypeLabels[type]} endpoint` : `Add ${endpointTypeLabels[type]} endpoint`

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-900/60">
      <div className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-2xl">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button onClick={onClose} className={button.icon}>
            <X className="size-5" />
          </button>
        </div>
        <p className="text-sm text-slate-500 mt-1">{providerName}</p>
        <div className="mt-4 space-y-3">
          <ProviderEndpointForm
            mode="create"
            type={type}
            endpoint={sourceEndpoint}
            saving={busy}
            submitLabel={sourceEndpoint ? 'Clone endpoint' : 'Add endpoint'}
            onClose={onClose}
            onSubmit={onSubmit}
          />
        </div>
      </div>
    </div>
  )
}
