import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, EyeOff, Globe, KeyRound, Loader2, Trash2 } from 'lucide-react'

import { fulfillRequestedSecrets, removeRequestedSecrets } from '../../api/agentChat'
import { HttpError } from '../../api/http'
import { fetchAgentSecrets, type AgentSecretListResponse, type RequestedSecretDTO } from '../../api/secrets'
import { SettingsBanner } from '../agentSettings/SettingsBanner'
import { getSettingsActionButtonClassName } from '../agentSettings/SettingsControls'
import { InlineStatusBanner } from '../common/InlineStatusBanner'
import { getSettingsSurfaceClassName } from '../common/SettingsSurface'
import { EmbeddedAgentShellBackButton } from './EmbeddedAgentShellBackButton'
import { EmbeddedAgentShellPanel } from './EmbeddedAgentShellPanel'
import {
  EmbeddedPendingRequestState,
  EmbeddedPendingRequestSummary,
  formatPendingRequestDate,
  usePendingRequestSelection,
} from './PendingRequestPanelParts'

type EmbeddedAgentSecretRequestsPanelProps = {
  agentId: string
  agentName: string
  onBack?: () => void
  onOpenSecrets?: () => void
  onFulfillRequestedSecrets?: (values: Record<string, string>, makeGlobal: boolean) => Promise<void>
  onRemoveRequestedSecrets?: (secretIds: string[]) => Promise<void>
}

type RequestErrors = {
  message: string | null
  fieldErrors: Record<string, string>
}

const EMPTY_REQUESTS: RequestedSecretDTO[] = []

function formatSecretScope(secret: RequestedSecretDTO): string {
  if (secret.secret_type === 'env_var' || secret.domain_pattern === '__gobii_env_var__') {
    return 'Environment variable'
  }
  return `Credential for ${secret.domain_pattern}`
}

function readBodyObject(body: unknown): Record<string, unknown> | null {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return null
  }
  return body as Record<string, unknown>
}

function readErrorMessage(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) {
    return value
  }
  if (Array.isArray(value)) {
    const first = value.find((item) => typeof item === 'string' && item.trim())
    return typeof first === 'string' ? first : null
  }
  return null
}

function parseRequestErrors(error: unknown, fallback: string): RequestErrors {
  if (!(error instanceof HttpError)) {
    return {
      message: error instanceof Error ? error.message : fallback,
      fieldErrors: {},
    }
  }

  const body = readBodyObject(error.body)
  if (!body) {
    return { message: fallback, fieldErrors: {} }
  }

  const fieldErrors: Record<string, string> = {}
  const rawErrors = readBodyObject(body.errors)
  if (rawErrors) {
    Object.entries(rawErrors).forEach(([field, value]) => {
      const message = readErrorMessage(value)
      if (message) {
        fieldErrors[field] = message
      }
    })
  }

  const message = (
    readErrorMessage(body.error)
    ?? fieldErrors.__all__
    ?? fieldErrors.make_global
    ?? fallback
  )

  return { message, fieldErrors }
}

export function EmbeddedAgentSecretRequestsPanel({
  agentId,
  agentName,
  onBack,
  onOpenSecrets,
  onFulfillRequestedSecrets,
  onRemoveRequestedSecrets,
}: EmbeddedAgentSecretRequestsPanelProps) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['agent-secrets', agentId] as const, [agentId])
  const [secretValues, setSecretValues] = useState<Record<string, string>>({})
  const [makeGlobal, setMakeGlobal] = useState(false)
  const [busyAction, setBusyAction] = useState<'save' | 'remove' | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [requestErrors, setRequestErrors] = useState<RequestErrors>({ message: null, fieldErrors: {} })

  const { data, isLoading, error, refetch } = useQuery<AgentSecretListResponse>({
    queryKey,
    queryFn: ({ signal }) => fetchAgentSecrets(`/console/api/agents/${agentId}/secrets/`, signal),
    enabled: Boolean(agentId),
    refetchOnWindowFocus: false,
  })

  const requests = data?.requested_secrets ?? EMPTY_REQUESTS
  const {
    selectedIds,
    selectedItems: selectedRequests,
    allSelected,
    toggleSelected,
    selectAll,
    clearSelected,
    removeSelected,
  } = usePendingRequestSelection(requests)
  const busy = busyAction !== null

  useEffect(() => {
    setSecretValues((current) => {
      const nextValues: Record<string, string> = {}
      requests.forEach((request) => {
        nextValues[request.id] = current[request.id] ?? ''
      })
      return nextValues
    })
  }, [requests])

  const refreshAfterMutation = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey })
    void queryClient.invalidateQueries({ queryKey: ['agent-settings', agentId], exact: true })
    void queryClient.invalidateQueries({ queryKey: ['agent-quick-settings', agentId], exact: true })
    await refetch()
  }, [agentId, queryClient, queryKey, refetch])

  const updateSecretValue = useCallback((secretId: string, value: string) => {
    setSecretValues((current) => ({ ...current, [secretId]: value }))
    setRequestErrors((current) => {
      if (!current.fieldErrors[secretId]) {
        return current
      }
      const fieldErrors = { ...current.fieldErrors }
      delete fieldErrors[secretId]
      return { ...current, fieldErrors }
    })
  }, [])

  const handleSave = useCallback(async () => {
    if (busy) {
      return
    }
    const values: Record<string, string> = {}
    Object.entries(secretValues).forEach(([secretId, value]) => {
      const trimmedValue = value.trim()
      if (trimmedValue) {
        values[secretId] = trimmedValue
      }
    })
    if (Object.keys(values).length === 0) {
      setRequestErrors({ message: 'Enter at least one requested secret value.', fieldErrors: {} })
      return
    }

    setBusyAction('save')
    setRequestErrors({ message: null, fieldErrors: {} })
    setSuccessMessage(null)
    try {
      if (onFulfillRequestedSecrets) {
        await onFulfillRequestedSecrets(values, makeGlobal)
      } else {
        await fulfillRequestedSecrets(agentId, { values, make_global: makeGlobal })
      }
      setSuccessMessage('Secret values saved.')
      setSecretValues({})
      await refreshAfterMutation()
    } catch (err) {
      setRequestErrors(parseRequestErrors(err, 'Unable to save requested secrets.'))
    } finally {
      setBusyAction(null)
    }
  }, [agentId, busy, makeGlobal, onFulfillRequestedSecrets, refreshAfterMutation, secretValues])

  const handleRemove = useCallback(async (secretIds: string[]) => {
    if (!secretIds.length || busy) {
      return
    }
    setBusyAction('remove')
    setRequestErrors({ message: null, fieldErrors: {} })
    setSuccessMessage(null)
    try {
      if (onRemoveRequestedSecrets) {
        await onRemoveRequestedSecrets(secretIds)
      } else {
        await removeRequestedSecrets(agentId, { secret_ids: secretIds })
      }
      removeSelected(secretIds)
      setSuccessMessage(secretIds.length === 1 ? 'Secret request removed.' : 'Secret requests removed.')
      await refreshAfterMutation()
    } catch (err) {
      setRequestErrors(parseRequestErrors(err, 'Unable to remove requested secrets.'))
    } finally {
      setBusyAction(null)
    }
  }, [agentId, busy, onRemoveRequestedSecrets, refreshAfterMutation, removeSelected])

  return (
    <EmbeddedAgentShellPanel>
      <SettingsBanner
        variant="embedded"
        leading={<EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to settings" />}
        eyebrow="Agent settings"
        title="Secret Requests"
        subtitle={`Provide values ${agentName} requested during setup or task execution.`}
      />

      <div className="mt-4 space-y-4 pb-8">
        {successMessage ? (
          <InlineStatusBanner variant="success" surface="embedded" icon={Check}>
            <p>{successMessage}</p>
          </InlineStatusBanner>
        ) : null}

        {requestErrors.message ? (
          <InlineStatusBanner variant="error" surface="embedded" icon={AlertTriangle}>
            <p>{requestErrors.message}</p>
          </InlineStatusBanner>
        ) : null}

        <EmbeddedPendingRequestState
          isLoading={isLoading}
          error={error}
          isEmpty={requests.length === 0}
          loadingLabel="Loading secret requests..."
          errorTitle="Unable to load secret requests."
          emptyTitle="No pending secret requests"
          emptyDescription="New requests will appear here when this agent needs a credential or environment variable."
          emptyAction={onOpenSecrets ? (
            <div className="flex flex-wrap justify-center gap-2">
              <button
                type="button"
                onClick={onOpenSecrets}
                className={getSettingsActionButtonClassName({ tone: 'primary' })}
              >
                Manage secrets
              </button>
            </div>
          ) : null}
        >
          <>
            <EmbeddedPendingRequestSummary
              count={requests.length}
              noun="secret"
              description="Fill one or more values, then save. Blank rows stay pending."
              actions={(
                <>
                  <button
                    type="button"
                    onClick={allSelected ? clearSelected : selectAll}
                    disabled={busy}
                    className={getSettingsActionButtonClassName()}
                  >
                    {allSelected ? 'Clear all' : 'Select all'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleRemove(selectedRequests.map((request) => request.id))}
                    disabled={busy || selectedRequests.length === 0}
                    className={getSettingsActionButtonClassName({ tone: 'danger' })}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    {busyAction === 'remove' ? 'Removing...' : `Remove selected${selectedRequests.length ? ` (${selectedRequests.length})` : ''}`}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleSave()}
                    disabled={busy}
                    className={getSettingsActionButtonClassName({ tone: 'success' })}
                  >
                    {busyAction === 'save' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Check className="h-4 w-4" aria-hidden="true" />}
                    {busyAction === 'save' ? 'Saving...' : 'Save values'}
                  </button>
                </>
              )}
              footer={(
                <label className="mt-4 flex items-start gap-3 rounded-xl border border-slate-200/15 bg-slate-900/25 px-3 py-3 text-sm text-slate-200">
                  <input
                    type="checkbox"
                    checked={makeGlobal}
                    onChange={(event) => setMakeGlobal(event.currentTarget.checked)}
                    disabled={busy}
                    className="mt-0.5 h-4 w-4 rounded border-slate-400 bg-slate-950 text-sky-500 focus:ring-sky-400"
                  />
                  <Globe className="mt-0.5 h-4 w-4 shrink-0 text-sky-300" aria-hidden="true" />
                  <span>Save as global secrets for this context so other agents can reuse matching keys.</span>
                </label>
              )}
            />

            <div className="space-y-3">
              {requests.map((request) => {
                const requestedAt = formatPendingRequestDate(request.created_at)
                const selected = selectedIds.has(request.id)
                const fieldError = requestErrors.fieldErrors[request.id]

                return (
                  <article
                    key={request.id}
                    className={getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none', className: 'px-4 py-4 text-slate-100' })}
                  >
                    <div className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(event) => toggleSelected(request.id, event.currentTarget.checked)}
                        disabled={busy}
                        aria-label={`Select ${request.name}`}
                        className="mt-1 h-4 w-4 rounded border-slate-400 bg-slate-950 text-sky-500 focus:ring-sky-400"
                      />
                      <div className="min-w-0 flex-1 space-y-4">
                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                          <div className="min-w-0">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-slate-200/20 bg-slate-900/45 text-slate-200">
                                {request.secret_type === 'env_var' ? <KeyRound className="h-4 w-4" aria-hidden="true" /> : <EyeOff className="h-4 w-4" aria-hidden="true" />}
                              </span>
                              <div className="min-w-0">
                                <h2 className="truncate text-sm font-semibold text-slate-50">{request.name}</h2>
                                <p className="truncate text-xs text-slate-400">Key: {request.key}</p>
                              </div>
                            </div>
                            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                              <span className="rounded-full border border-slate-200/15 bg-slate-900/35 px-2 py-1">
                                {formatSecretScope(request)}
                              </span>
                              {requestedAt ? <span>Requested {requestedAt}</span> : null}
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => void handleRemove([request.id])}
                            disabled={busy}
                            className={getSettingsActionButtonClassName({ tone: 'danger', className: 'shrink-0' })}
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                            Remove
                          </button>
                        </div>

                        {request.description ? (
                          <p className="text-sm text-slate-300">{request.description}</p>
                        ) : null}

                        <div>
                          <label className="block text-xs font-semibold uppercase tracking-[0.18em] text-slate-400" htmlFor={`secret-request-${request.id}`}>
                            Secret value
                          </label>
                          <input
                            id={`secret-request-${request.id}`}
                            type="password"
                            value={secretValues[request.id] ?? ''}
                            onChange={(event) => updateSecretValue(request.id, event.currentTarget.value)}
                            disabled={busy}
                            autoComplete="new-password"
                            className="mt-2 block w-full rounded-xl border border-slate-200/20 bg-slate-950/45 px-3 py-2.5 text-sm text-slate-100 shadow-none outline-none transition focus:border-sky-300/60 focus:ring-2 focus:ring-sky-400/20 disabled:cursor-not-allowed disabled:opacity-60"
                            placeholder={`Enter value for ${request.name}`}
                          />
                          {fieldError ? <p className="mt-2 text-sm text-rose-200">{fieldError}</p> : null}
                        </div>
                      </div>
                    </div>
                  </article>
                )
              })}
            </div>
          </>
        </EmbeddedPendingRequestState>
      </div>
    </EmbeddedAgentShellPanel>
  )
}
