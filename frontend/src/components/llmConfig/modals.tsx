import { AlertCircle, Loader2, LoaderCircle, Plus, X } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import * as llmApi from '../../api/llmConfig'
import { HttpError } from '../../api/http'
import {
  button,
  type ConfirmDialogConfig,
  formatNullableNumber,
  type Tier,
  type TierScope,
} from './shared'

export function AddEndpointModal({
  tier,
  scope,
  choices,
  onAdd,
  onClose,
  busy,
}: {
  tier: Tier
  scope: TierScope
  choices: llmApi.EndpointChoices
  onAdd: (selection: { endpointId: string; extractionEndpointId?: string | null }) => Promise<void> | void
  onClose: () => void
  busy?: boolean
}) {
  const endpoints = scope === 'browser'
    ? choices.browser_endpoints
    : scope === 'embedding'
      ? choices.embedding_endpoints
      : scope === 'file_handler'
        ? choices.file_handler_endpoints
        : scope === 'image_generation'
          ? choices.image_generation_endpoints
          : scope === 'video_generation'
            ? choices.video_generation_endpoints
          : choices.persistent_endpoints
  const [selected, setSelected] = useState(endpoints[0]?.id || '')
  const [extractionSelected, setExtractionSelected] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)
  const isSubmitting = Boolean(busy || submitting)

  const handleAdd = async () => {
    if (!selected) return
    setSubmitting(true)
    try {
      await onAdd({ endpointId: selected, extractionEndpointId: scope === 'browser' ? (extractionSelected || null) : undefined })
      onClose()
    } catch {
      // feedback already shown
    } finally {
      setSubmitting(false)
    }
  }
  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-900/60">
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Add endpoint to {tier.name}</h3>
          <button onClick={onClose} className={button.icon}>
            <X className="size-5" />
          </button>
        </div>
        <div className="mt-4">
          {endpoints.length === 0 ? (
            <p className="text-sm text-slate-500">No endpoints available for this tier.</p>
          ) : (
            <>
              <label className="text-sm font-medium text-slate-700">Endpoint</label>
              <select
                className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                value={selected}
                onChange={(event) => setSelected(event.target.value)}
              >
                {endpoints.map((endpoint) => (
                  <option key={endpoint.id} value={endpoint.id}>
                    {endpoint.label || endpoint.model}
                  </option>
                ))}
              </select>
              {scope === 'browser' ? (
                <div className="mt-4 space-y-1">
                  <label className="text-sm font-medium text-slate-700">Extraction endpoint (optional)</label>
                  <select
                    className="mt-1 block w-full rounded-lg border-slate-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                    value={extractionSelected}
                    onChange={(event) => setExtractionSelected(event.target.value)}
                  >
                    <option value="">No separate extraction model</option>
                    {endpoints.map((endpoint) => (
                      <option key={endpoint.id} value={endpoint.id}>
                        {endpoint.label || endpoint.model}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-slate-500">If set, page extraction uses this endpoint; otherwise it falls back to the primary model.</p>
                </div>
              ) : null}
            </>
          )}
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <button type="button" className={button.secondary} onClick={onClose} disabled={isSubmitting}>
            Cancel
          </button>
          <button
            type="button"
            className={button.primary}
            onClick={handleAdd}
            disabled={!selected || isSubmitting}
          >
            {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />} Add endpoint
          </button>
        </div>
      </div>
    </div>
  )
}

function ConfirmActionModal({
  title,
  message,
  confirmLabel,
  cancelLabel,
  intent,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string
  message: ReactNode
  confirmLabel: string
  cancelLabel: string
  intent: 'danger' | 'primary'
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const accentClasses =
    intent === 'danger'
      ? { iconBg: 'bg-rose-100 text-rose-600', button: 'inline-flex items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-700 focus:outline-none focus:ring-2 focus:ring-rose-500/40 disabled:opacity-50' }
      : { iconBg: 'bg-blue-100 text-blue-600', button: button.primary }
  return (
    <div className="fixed inset-0 z-[210] flex items-center justify-center overflow-y-auto bg-slate-900/60 p-4">
      <div className="max-h-[calc(100vh-2rem)] w-full max-w-2xl overflow-y-auto overflow-x-hidden rounded-2xl bg-white p-6 shadow-xl space-y-4">
        <div className="flex items-start gap-3">
          <div className={`shrink-0 rounded-full p-2 ${accentClasses.iconBg}`}>
            <AlertCircle className="size-5" />
          </div>
          <div className="min-w-0 flex-1 space-y-1">
            <h3 className="break-words text-lg font-semibold text-slate-900">{title}</h3>
            <div className="min-w-0 text-sm text-slate-600">{message}</div>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-3 pt-2">
          <button type="button" className={button.secondary} onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button type="button" className={accentClasses.button} onClick={onConfirm} disabled={busy}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : null}
            <span>{confirmLabel}</span>
          </button>
        </div>
      </div>
    </div>
  )
}

export function EndpointDeleteMessage({
  usage,
}: {
  usage: llmApi.EndpointTierUsage[]
}) {
  if (!usage.length) {
    return <span>This removes the endpoint from the provider.</span>
  }

  return (
    <div className="min-w-0 space-y-3">
      <p>This endpoint is currently used by {usage.length} routing assignment{usage.length === 1 ? '' : 's'}.</p>
      <div className="space-y-2">
        {usage.map((entry) => (
          <div key={`${entry.source}-${entry.id}`} className="min-w-0 rounded-lg border border-slate-200 p-3">
            <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="break-words font-medium text-slate-900">{entry.routing_profile}</p>
                <p className="text-xs text-slate-500">
                  {entry.source === 'browser_policy'
                    ? 'Browser policy'
                    : entry.source === 'persistent_policy'
                      ? 'Persistent routing'
                      : 'Routing profile'}
                  {entry.routing_profile_active ? ' · active' : ''}
                  {entry.role === 'extraction' ? ' · extraction endpoint' : ''}
                  {entry.role === 'eval_judge' ? ' · eval judge' : ''}
                  {entry.role === 'summarization' ? ' · summarization' : ''}
                  {entry.role === 'agent_judge' ? ' · agent judge' : ''}
                </p>
              </div>
              {typeof entry.weight === 'number' ? (
                <span className="shrink-0 rounded-full border border-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600">
                  weight {formatNullableNumber(entry.weight)}
                </span>
              ) : null}
            </div>
            <p className="mt-2 break-words text-sm text-slate-700">{entry.tier}</p>
            {entry.description ? <p className="mt-1 break-words text-xs text-slate-500">{entry.description}</p> : null}
          </div>
        ))}
      </div>
      <p>Confirming will remove the endpoint from these assignments, then delete it.</p>
    </div>
  )
}

export function getEndpointDeleteConflictUsage(error: unknown): llmApi.EndpointTierUsage[] | null {
  if (!(error instanceof HttpError) || error.status !== 409) {
    return null
  }
  const body = error.body
  if (!body || typeof body !== 'object' || !('code' in body) || !('tier_usage' in body)) {
    return null
  }
  const payload = body as { code?: unknown; tier_usage?: unknown }
  if (payload.code !== 'endpoint_in_tiers' || !Array.isArray(payload.tier_usage)) {
    return null
  }
  return payload.tier_usage as llmApi.EndpointTierUsage[]
}

export function ConfirmModalWrapper({
  options,
  onResolve,
  onReject,
  onClose,
}: {
  options: ConfirmDialogConfig
  onResolve: () => void
  onReject: (error?: unknown) => void
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const {
    title,
    message,
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    intent = 'danger',
    onConfirm,
  } = options

  return createPortal(
    <ConfirmActionModal
      title={title}
      message={message}
      confirmLabel={confirmLabel}
      cancelLabel={cancelLabel}
      intent={intent}
      busy={busy}
      onConfirm={async () => {
        setBusy(true)
        try {
          await onConfirm?.()
          onResolve()
          onClose()
        } catch (error) {
          onReject(error)
          onClose()
        } finally {
          setBusy(false)
        }
      }}
      onCancel={() => {
        onResolve()
        onClose()
      }}
    />,
    document.body,
  )
}

export function CreateProfileModal({
  onCreate,
  onClose,
}: {
  onCreate: (name: string) => Promise<unknown>
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    try {
      await onCreate(name.trim())
      onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h3 className="text-lg font-semibold text-slate-900">Create Routing Profile</h3>
          <button type="button" className={button.icon} onClick={onClose}>
            <X className="size-5" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Profile Name</label>
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g., Production, Staging, Eval A"
              className="w-full rounded-xl border border-slate-200 px-4 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              autoFocus
              disabled={submitting}
            />
            <p className="mt-1 text-xs text-slate-500">
              A unique identifier will be generated from the name.
            </p>
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className={button.secondary} onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className={button.primary} disabled={!name.trim() || submitting}>
              {submitting ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" />
                  Creating...
                </>
              ) : (
                'Create Profile'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}

export function EditProfileModal({
  profile,
  onSave,
  onClose,
}: {
  profile: {
    id: string
    display_name: string | null
    name: string
    description: string | null
  }
  onSave: (payload: { display_name: string; description: string }) => Promise<void>
  onClose: () => void
}) {
  const [displayName, setDisplayName] = useState(profile.display_name || profile.name)
  const [description, setDescription] = useState(profile.description || '')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!displayName.trim()) return
    setSubmitting(true)
    try {
      await onSave({ display_name: displayName.trim(), description: description.trim() })
      onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h3 className="text-lg font-semibold text-slate-900">Edit Routing Profile</h3>
          <button type="button" className={button.icon} onClick={onClose}>
            <X className="size-5" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Display Name</label>
            <input
              type="text"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="e.g., Production, Staging, Eval A"
              className="w-full rounded-xl border border-slate-200 px-4 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              autoFocus
              disabled={submitting}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Optional description for this profile"
              rows={3}
              className="w-full rounded-xl border border-slate-200 px-4 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 resize-none"
              disabled={submitting}
            />
          </div>
          <div className="flex justify-end gap-3">
            <button type="button" className={button.secondary} onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button
              type="submit"
              className={button.primary}
              disabled={!displayName.trim() || submitting}
            >
              {submitting ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" />
                  Saving...
                </>
              ) : (
                'Save Changes'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
