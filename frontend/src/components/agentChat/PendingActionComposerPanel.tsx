import { useEffect, useMemo, useState } from 'react'

import { ChevronLeft, ChevronRight, KeyRound, Mail, MessageSquareQuote, Zap } from 'lucide-react'

import { HttpError } from '../../api/http'
import type { PendingActionRequest } from '../../types/agentChat'
import { HumanInputComposerPanel } from './HumanInputComposerPanel'
import { PendingContactRequestsPanel, type PendingContactDraft } from './PendingContactRequestsPanel'
import { PendingRequestedSecretsPanel } from './PendingRequestedSecretsPanel'
import { PendingSpawnRequestPanel } from './PendingSpawnRequestPanel'

type HumanInputDraft = {
  requestId: string
  selectedOptionKey?: string
  freeText?: string
}

type PendingActionComposerPanelProps = {
  actions: PendingActionRequest[]
  activeActionId: string | null
  onActiveActionChange: (actionId: string) => void
  disabled?: boolean
  activeHumanInputRequestId: string | null
  draftHumanInputResponses?: Record<string, HumanInputDraft>
  busyHumanInputRequestId?: string | null
  onActiveHumanInputRequestChange: (requestId: string) => void
  onSelectHumanInputOption: (requestId: string, optionKey: string) => Promise<void> | void
  onResolveSpawnRequest?: (decisionApiUrl: string, decision: 'approve' | 'decline') => Promise<void>
  onFulfillRequestedSecrets?: (values: Record<string, string>, makeGlobal: boolean) => Promise<void>
  onRemoveRequestedSecrets?: (secretIds: string[]) => Promise<void>
  onResolveContactRequests?: (
    responses: Array<{
      requestId: string
      decision: 'approve' | 'decline'
      allowInbound: boolean
      allowOutbound: boolean
      canConfigure: boolean
    }>
  ) => Promise<void>
}

function parseInlineError(error: unknown): string {
  if (error instanceof HttpError && error.body && typeof error.body === 'object') {
    const body = error.body as Record<string, unknown>
    if (typeof body.error === 'string' && body.error.trim()) {
      return body.error
    }
    if (body.errors && typeof body.errors === 'object') {
      const firstFieldErrors = Object.values(body.errors as Record<string, unknown>)
        .flatMap((value) => (Array.isArray(value) ? value : [value]))
        .map((value) => String(value))
        .find((value) => value.trim().length > 0)
      if (firstFieldErrors) {
        return firstFieldErrors
      }
    }
  }
  return error instanceof Error ? error.message : 'Something went wrong.'
}

function actionHeading(action: PendingActionRequest): string {
  switch (action.kind) {
    case 'human_input':
      return action.requests[0]?.question ?? 'Needs your reply'
    case 'spawn_request':
      return 'Create Agent'
    case 'requested_secrets':
      return action.secrets[0]?.name ?? 'Requested secret'
    case 'contact_requests':
      return action.requests[0]?.name || action.requests[0]?.address || 'Contact approval'
    default:
      return 'Pending action'
  }
}

function actionMeta(action: PendingActionRequest): string | null {
  switch (action.kind) {
    case 'human_input':
      return null
    case 'spawn_request':
      return action.requestReason || null
    case 'requested_secrets':
      return action.secrets[0]?.key || action.secrets[0]?.name || null
    case 'contact_requests': {
      const request = action.requests[0]
      const address = request?.name ? (request?.address ?? null) : null
      return [address, request?.purpose].filter(Boolean).join(' · ') || null
    }
    default:
      return null
  }
}

function actionIcon(action: PendingActionRequest) {
  switch (action.kind) {
    case 'human_input':
      return MessageSquareQuote
    case 'spawn_request':
      return Zap
    case 'requested_secrets':
      return KeyRound
    case 'contact_requests':
      return Mail
    default:
      return MessageSquareQuote
  }
}

function getActiveHumanInputRequest(
  action: PendingActionRequest | null,
  activeHumanInputRequestId: string | null,
) {
  if (!action || action.kind !== 'human_input') {
    return null
  }
  return action.requests.find((request) => request.id === activeHumanInputRequestId) ?? action.requests[0] ?? null
}

export function PendingActionComposerPanel({
  actions,
  activeActionId,
  onActiveActionChange,
  disabled = false,
  activeHumanInputRequestId,
  draftHumanInputResponses = {},
  busyHumanInputRequestId = null,
  onActiveHumanInputRequestChange,
  onSelectHumanInputOption,
  onResolveSpawnRequest,
  onFulfillRequestedSecrets,
  onRemoveRequestedSecrets,
  onResolveContactRequests,
}: PendingActionComposerPanelProps) {
  const [busySpawnDecision, setBusySpawnDecision] = useState<'approve' | 'decline' | null>(null)
  const [spawnError, setSpawnError] = useState<string | null>(null)
  const [secretValues, setSecretValues] = useState<Record<string, string>>({})
  const [makeGlobal, setMakeGlobal] = useState(false)
  const [busySecretsAction, setBusySecretsAction] = useState<'save' | 'remove' | null>(null)
  const [secretError, setSecretError] = useState<string | null>(null)
  const [contactDrafts, setContactDrafts] = useState<Record<string, PendingContactDraft>>({})
  const [busyContacts, setBusyContacts] = useState(false)
  const [contactError, setContactError] = useState<string | null>(null)

  const activeAction = actions.find((action) => action.id === activeActionId) ?? actions[0] ?? null
  const activeHumanInputRequest = getActiveHumanInputRequest(activeAction, activeHumanInputRequestId)
  const activeIndex = Math.max(0, actions.findIndex((action) => action.id === activeAction?.id))
  const ActiveIcon = activeAction ? actionIcon(activeAction) : MessageSquareQuote
  const activeActionHeading = activeAction?.kind === 'human_input'
    ? (activeHumanInputRequest?.question ?? 'Needs your reply')
    : (activeAction ? actionHeading(activeAction) : 'Pending action')
  const activeActionMeta = activeAction?.kind === 'human_input'
    ? null
    : (activeAction ? actionMeta(activeAction) : null)

  useEffect(() => {
    if (activeAction?.kind !== 'requested_secrets') {
      return
    }
    const activeSecretIds = new Set(activeAction.secrets.map((secret) => secret.id))
    setSecretValues((current) => Object.fromEntries(Object.entries(current).filter(([secretId]) => activeSecretIds.has(secretId))))
  }, [activeAction])

  useEffect(() => {
    if (activeAction?.kind !== 'contact_requests') {
      return
    }
    setContactDrafts((current) => {
      const nextDrafts: Record<string, PendingContactDraft> = {}
      activeAction.requests.forEach((request) => {
        nextDrafts[request.id] = current[request.id] ?? {
          allowInbound: request.allowInbound,
          allowOutbound: request.allowOutbound,
        }
      })
      return nextDrafts
    })
  }, [activeAction])

  const secretValuesToSubmit = useMemo(() => {
    if (activeAction?.kind !== 'requested_secrets') {
      return {}
    }
    return Object.fromEntries(
      activeAction.secrets
        .map((secret) => [secret.id, (secretValues[secret.id] ?? '').trim()] as const)
        .filter(([, value]) => value.length > 0),
    )
  }, [activeAction, secretValues])

  if (!activeAction) {
    return null
  }

  const handleResolveSpawn = async (decision: 'approve' | 'decline') => {
    if (activeAction.kind !== 'spawn_request' || !activeAction.decisionApiUrl || !onResolveSpawnRequest || busySpawnDecision) {
      return
    }
    setBusySpawnDecision(decision)
    setSpawnError(null)
    try {
      await onResolveSpawnRequest(activeAction.decisionApiUrl, decision)
    } catch (error) {
      setSpawnError(parseInlineError(error))
    } finally {
      setBusySpawnDecision(null)
    }
  }

  const handleSaveSecrets = async () => {
    if (activeAction.kind !== 'requested_secrets' || !onFulfillRequestedSecrets || busySecretsAction) {
      return
    }
    if (Object.keys(secretValuesToSubmit).length === 0) {
      setSecretError('Enter at least one secret value to save.')
      return
    }
    setBusySecretsAction('save')
    setSecretError(null)
    try {
      await onFulfillRequestedSecrets(secretValuesToSubmit, makeGlobal)
      setSecretValues({})
      setMakeGlobal(false)
    } catch (error) {
      setSecretError(parseInlineError(error))
    } finally {
      setBusySecretsAction(null)
    }
  }

  const handleRemoveSecrets = async () => {
    if (activeAction.kind !== 'requested_secrets' || !onRemoveRequestedSecrets || busySecretsAction) {
      return
    }
    const secretIds = activeAction.secrets.map((secret) => secret.id)
    if (!secretIds.length) {
      setSecretError('Requested secret could not be found.')
      return
    }
    setBusySecretsAction('remove')
    setSecretError(null)
    try {
      await onRemoveRequestedSecrets(secretIds)
    } catch (error) {
      setSecretError(parseInlineError(error))
    } finally {
      setBusySecretsAction(null)
    }
  }

  const handleResolveContacts = async (decision: 'approve' | 'decline', requestId: string) => {
    if (activeAction.kind !== 'contact_requests' || !onResolveContactRequests || busyContacts) {
      return
    }
    const request = activeAction.requests.find((candidate) => candidate.id === requestId)
    if (!request) {
      return
    }
    const draft = contactDrafts[request.id] ?? {
      allowInbound: request.allowInbound,
      allowOutbound: request.allowOutbound,
    }
    setBusyContacts(true)
    setContactError(null)
    try {
      await onResolveContactRequests([
        {
          requestId: request.id,
          decision,
          allowInbound: draft.allowInbound,
          allowOutbound: draft.allowOutbound,
          canConfigure: false,
        },
      ])
    } catch (error) {
      setContactError(parseInlineError(error))
    } finally {
      setBusyContacts(false)
    }
  }

  return (
    <section className="bg-white px-3 py-3 text-slate-800" aria-label="Pending action request">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-sky-100 text-sky-700">
            <ActiveIcon className="h-4 w-4" aria-hidden="true" />
          </span>
          <div className={`min-w-0 ${activeActionMeta ? '' : 'flex min-h-9 items-center'}`}>
            <p className="min-w-0 text-[0.95rem] font-semibold leading-6 tracking-[-0.02em] text-slate-900">
              {activeActionHeading}
            </p>
            {activeActionMeta ? (
              <p className="text-xs text-slate-600">
                {activeActionMeta}
              </p>
            ) : null}
          </div>
        </div>
        {actions.length > 1 ? (
          <div className="flex shrink-0 items-center gap-1.5 text-sm text-slate-500">
            <button
              type="button"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-35"
              onClick={() => onActiveActionChange(actions[Math.max(0, activeIndex - 1)].id)}
              disabled={disabled || activeIndex === 0}
              aria-label="Previous pending action"
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </button>
            <span className="min-w-[3.25rem] text-center text-[10px] font-medium uppercase tracking-[0.14em] text-slate-400">
              {activeIndex + 1} of {actions.length}
            </span>
            <button
              type="button"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-35"
              onClick={() => onActiveActionChange(actions[Math.min(actions.length - 1, activeIndex + 1)].id)}
              disabled={disabled || activeIndex >= actions.length - 1}
              aria-label="Next pending action"
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ) : null}
      </div>

      <div className="mt-3">
        {activeAction.kind === 'human_input' ? (
          <HumanInputComposerPanel
            requests={activeAction.requests}
            activeRequestId={activeHumanInputRequestId}
            draftResponses={draftHumanInputResponses}
            disabled={disabled}
            busyRequestId={busyHumanInputRequestId}
            showQuestion={false}
            onActiveRequestChange={onActiveHumanInputRequestChange}
            onSelectOption={onSelectHumanInputOption}
          />
        ) : null}

        {activeAction.kind === 'spawn_request' ? (
          <PendingSpawnRequestPanel
            action={activeAction}
            disabled={disabled || !onResolveSpawnRequest}
            busyDecision={busySpawnDecision}
            error={spawnError}
            onResolve={handleResolveSpawn}
          />
        ) : null}

        {activeAction.kind === 'requested_secrets' ? (
          <PendingRequestedSecretsPanel
            action={activeAction}
            disabled={disabled || (!onFulfillRequestedSecrets && !onRemoveRequestedSecrets)}
            busyAction={busySecretsAction}
            error={secretError}
            secretValues={secretValues}
            makeGlobal={makeGlobal}
            onSecretValueChange={(secretId, value) => {
              setSecretValues((current) => ({ ...current, [secretId]: value }))
            }}
            onMakeGlobalChange={setMakeGlobal}
            onSave={handleSaveSecrets}
            onRemove={handleRemoveSecrets}
          />
        ) : null}

        {activeAction.kind === 'contact_requests' ? (
          <PendingContactRequestsPanel
            action={activeAction}
            disabled={disabled || !onResolveContactRequests}
            busy={busyContacts}
            error={contactError}
            contactDrafts={contactDrafts}
            onContactDraftChange={(requestId, nextDraft) => {
              setContactDrafts((current) => ({ ...current, [requestId]: nextDraft }))
            }}
            onSubmit={handleResolveContacts}
          />
        ) : null}
      </div>
    </section>
  )
}
