import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Check, Inbox, Loader2, Mail, Phone, X } from 'lucide-react'

import { fetchContactRequests, resolveContactRequests } from '../../api/agentChat'
import { SettingsBanner } from '../agentSettings/SettingsBanner'
import { InlineStatusBanner } from '../common/InlineStatusBanner'
import { getSettingsSurfaceClassName } from '../common/SettingsSurface'
import type { PendingContactRequest } from '../../types/agentChat'
import { EmbeddedAgentShellBackButton } from './EmbeddedAgentShellBackButton'
import { EmbeddedAgentShellPanel } from './EmbeddedAgentShellPanel'
import type { PendingContactDraft } from './PendingContactRequestsPanel'

type ContactRequestResolution = {
  requestId: string
  decision: 'approve' | 'decline'
  allowInbound: boolean
  allowOutbound: boolean
  canConfigure: boolean
  smsContactPermissionAttested?: boolean
}

type EmbeddedAgentContactRequestsPanelProps = {
  agentId: string
  agentName: string
  onBack?: () => void
  onResolveContactRequests?: (responses: ContactRequestResolution[]) => Promise<void>
}

const EMPTY_CONTACT_REQUESTS: PendingContactRequest[] = []

function makeContactDraft(request: PendingContactRequest): PendingContactDraft {
  return {
    allowInbound: request.allowInbound,
    allowOutbound: request.allowOutbound,
    smsContactPermissionAttested: Boolean(request.smsContactPermissionAttested),
  }
}

function formatDateTime(value?: string | null): string | null {
  if (!value) {
    return null
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return null
  }
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

function formatChannel(channel: string): string {
  return channel.toLowerCase() === 'sms' ? 'SMS' : channel.charAt(0).toUpperCase() + channel.slice(1)
}

function contactRequiresSmsAttestation(request: PendingContactRequest, draft: PendingContactDraft): boolean {
  return request.channel === 'sms' && !draft.smsContactPermissionAttested
}

export function EmbeddedAgentContactRequestsPanel({
  agentId,
  agentName,
  onBack,
  onResolveContactRequests,
}: EmbeddedAgentContactRequestsPanelProps) {
  const [drafts, setDrafts] = useState<Record<string, PendingContactDraft>>({})
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['agent-contact-requests', agentId],
    queryFn: () => fetchContactRequests(agentId),
    enabled: Boolean(agentId),
    refetchOnWindowFocus: false,
  })

  const requests = data?.requests ?? EMPTY_CONTACT_REQUESTS

  useEffect(() => {
    setDrafts((current) => {
      const nextDrafts: Record<string, PendingContactDraft> = {}
      requests.forEach((request) => {
        nextDrafts[request.id] = current[request.id] ?? makeContactDraft(request)
      })
      return nextDrafts
    })
    setSelectedIds((current) => {
      const requestIds = new Set(requests.map((request) => request.id))
      return new Set([...current].filter((requestId) => requestIds.has(requestId)))
    })
  }, [requests])

  const selectedRequests = useMemo(
    () => requests.filter((request) => selectedIds.has(request.id)),
    [requests, selectedIds],
  )
  const selectedApprovalBlocked = selectedRequests.some((request) => (
    contactRequiresSmsAttestation(request, drafts[request.id] ?? makeContactDraft(request))
  ))
  const allSelected = requests.length > 0 && selectedIds.size === requests.length

  const updateDraft = useCallback((requestId: string, nextDraft: PendingContactDraft) => {
    setDrafts((current) => ({ ...current, [requestId]: nextDraft }))
  }, [])

  const toggleSelected = useCallback((requestId: string, selected: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (selected) {
        next.add(requestId)
      } else {
        next.delete(requestId)
      }
      return next
    })
  }, [])

  const resolveRequests = useCallback(async (
    decision: 'approve' | 'decline',
    targetRequests: PendingContactRequest[],
  ) => {
    if (!targetRequests.length || busyAction) {
      return
    }
    if (decision === 'approve') {
      const blockedRequest = targetRequests.find((request) => (
        contactRequiresSmsAttestation(request, drafts[request.id] ?? makeContactDraft(request))
      ))
      if (blockedRequest) {
        setErrorMessage('Confirm SMS permission before approving selected SMS contacts.')
        return
      }
    }

    setBusyAction(`${decision}:${targetRequests.map((request) => request.id).join(',')}`)
    setErrorMessage(null)
    const responses = targetRequests.map((request) => {
      const draft = drafts[request.id] ?? makeContactDraft(request)
      return {
        requestId: request.id,
        decision,
        allowInbound: draft.allowInbound,
        allowOutbound: draft.allowOutbound,
        canConfigure: request.canConfigure,
        smsContactPermissionAttested: draft.smsContactPermissionAttested,
      }
    })

    try {
      if (onResolveContactRequests) {
        await onResolveContactRequests(responses)
      } else {
        await resolveContactRequests(agentId, {
          responses: responses.map((response) => ({
            request_id: response.requestId,
            decision: response.decision,
            allow_inbound: response.allowInbound,
            allow_outbound: response.allowOutbound,
            can_configure: response.canConfigure,
            sms_contact_permission_attested: response.smsContactPermissionAttested ?? null,
          })),
        })
      }
      setSelectedIds((current) => {
        const resolvedIds = new Set(targetRequests.map((request) => request.id))
        return new Set([...current].filter((requestId) => !resolvedIds.has(requestId)))
      })
      await refetch()
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Unable to update contact requests.')
    } finally {
      setBusyAction(null)
    }
  }, [agentId, busyAction, drafts, onResolveContactRequests, refetch])

  const handleSelectAll = useCallback(() => {
    setSelectedIds(new Set(requests.map((request) => request.id)))
  }, [requests])

  const handleClearSelected = useCallback(() => {
    setSelectedIds(new Set())
  }, [])

  const busy = busyAction !== null

  return (
    <EmbeddedAgentShellPanel>
      <SettingsBanner
        variant="embedded"
        leading={<EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to settings" />}
        eyebrow="Agent settings"
        title="Contact Requests"
        subtitle={`Review contacts waiting for ${agentName}.`}
      />

      <div className="mt-4 space-y-4 pb-8">
        {errorMessage ? (
          <InlineStatusBanner variant="error" surface="embedded" icon={AlertTriangle}>
            <p>{errorMessage}</p>
          </InlineStatusBanner>
        ) : null}

        {isLoading ? (
          <div className="flex min-h-[18rem] items-center justify-center text-sm text-slate-200/80">
            <div className="flex flex-col items-center gap-3 text-center">
              <Loader2 className="h-6 w-6 animate-spin text-slate-300/70" aria-hidden="true" />
              <p>Loading contact requests...</p>
            </div>
          </div>
        ) : error ? (
          <InlineStatusBanner variant="error" surface="embedded">
            <p className="font-medium">Unable to load contact requests.</p>
            <p className="mt-1 text-rose-100/75">Try opening this agent again.</p>
          </InlineStatusBanner>
        ) : requests.length === 0 ? (
          <div className={getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none', className: 'flex min-h-[18rem] items-center justify-center px-6 py-10 text-center' })}>
            <div className="max-w-sm space-y-4">
              <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200/20 bg-slate-900/45 text-slate-200">
                <Inbox className="h-5 w-5" aria-hidden="true" />
              </span>
              <div>
                <p className="text-sm font-semibold text-slate-100">No pending contact requests</p>
                <p className="mt-1 text-sm text-slate-300">New requests will appear here when this agent asks to contact someone.</p>
              </div>
            </div>
          </div>
        ) : (
          <>
            <div className={getSettingsSurfaceClassName({ variant: 'embedded', shadowClassName: 'shadow-none', className: 'flex flex-col gap-3 px-4 py-4 text-slate-100 sm:flex-row sm:items-center sm:justify-between' })}>
              <div>
                <p className="text-sm font-semibold text-slate-100">
                  {requests.length} pending contact request{requests.length === 1 ? '' : 's'}
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  {selectedIds.size} selected
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={allSelected ? handleClearSelected : handleSelectAll}
                  className="rounded-lg border border-slate-200/25 bg-slate-900/35 px-3 py-2 text-sm font-medium text-slate-100 transition-colors hover:border-slate-100/35 hover:bg-slate-900/55"
                >
                  {allSelected ? 'Clear all' : 'Select all'}
                </button>
                <button
                  type="button"
                  onClick={() => void resolveRequests('decline', selectedRequests)}
                  disabled={busy || selectedRequests.length === 0}
                  className="inline-flex items-center gap-2 rounded-lg border border-rose-300/25 bg-rose-950/35 px-3 py-2 text-sm font-semibold text-rose-100 transition-colors hover:border-rose-200/40 hover:bg-rose-900/50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                  Deny selected
                </button>
                <button
                  type="button"
                  onClick={() => void resolveRequests('approve', selectedRequests)}
                  disabled={busy || selectedRequests.length === 0 || selectedApprovalBlocked}
                  className="inline-flex items-center gap-2 rounded-lg border border-emerald-300/25 bg-emerald-900/50 px-3 py-2 text-sm font-semibold text-emerald-50 transition-colors hover:border-emerald-200/40 hover:bg-emerald-900/70 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Check className="h-4 w-4" aria-hidden="true" />
                  Approve selected
                </button>
              </div>
            </div>

            <div className="space-y-3">
              {requests.map((request) => {
                const draft = drafts[request.id] ?? makeContactDraft(request)
                const smsApprovalBlocked = contactRequiresSmsAttestation(request, draft)
                const requestedAt = formatDateTime(request.requestedAt)
                const expiresAt = formatDateTime(request.expiresAt)
                const heading = request.name || request.address
                const selected = selectedIds.has(request.id)
                const ChannelIcon = request.channel === 'sms' ? Phone : Mail

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
                        aria-label={`Select ${heading}`}
                        className="mt-1 h-4 w-4 rounded border-slate-400 bg-slate-950 text-sky-500 focus:ring-sky-400"
                      />
                      <div className="min-w-0 flex-1 space-y-4">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                          <div className="min-w-0">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-slate-200/20 bg-slate-900/45 text-slate-200">
                                <ChannelIcon className="h-4 w-4" aria-hidden="true" />
                              </span>
                              <div className="min-w-0">
                                <h2 className="truncate text-sm font-semibold text-slate-50">{heading}</h2>
                                {request.address !== heading ? (
                                  <p className="truncate text-xs text-slate-400">{request.address}</p>
                                ) : null}
                              </div>
                            </div>
                            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                              <span className="rounded-full border border-slate-200/15 bg-slate-900/35 px-2 py-1">
                                {formatChannel(request.channel)}
                              </span>
                              {requestedAt ? <span>Requested {requestedAt}</span> : null}
                              {expiresAt ? <span>Expires {expiresAt}</span> : null}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            <button
                              type="button"
                              onClick={() => void resolveRequests('decline', [request])}
                              disabled={busy}
                              className="inline-flex items-center gap-2 rounded-lg border border-rose-300/25 bg-rose-950/35 px-3 py-2 text-sm font-semibold text-rose-100 transition-colors hover:border-rose-200/40 hover:bg-rose-900/50 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <X className="h-4 w-4" aria-hidden="true" />
                              Deny
                            </button>
                            <button
                              type="button"
                              onClick={() => void resolveRequests('approve', [request])}
                              disabled={busy || smsApprovalBlocked}
                              className="inline-flex items-center gap-2 rounded-lg border border-emerald-300/25 bg-emerald-900/50 px-3 py-2 text-sm font-semibold text-emerald-50 transition-colors hover:border-emerald-200/40 hover:bg-emerald-900/70 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <Check className="h-4 w-4" aria-hidden="true" />
                              Approve
                            </button>
                          </div>
                        </div>

                        <div className="space-y-3 text-sm text-slate-300">
                          {request.purpose ? (
                            <p><span className="font-medium text-slate-200">Purpose:</span> {request.purpose}</p>
                          ) : null}
                          {request.reason ? (
                            <p className="whitespace-pre-line"><span className="font-medium text-slate-200">Reason:</span> {request.reason}</p>
                          ) : null}
                          {request.channel === 'sms' && request.smsContactPurpose ? (
                            <p>
                              <span className="font-medium text-slate-200">SMS purpose:</span>{' '}
                              {request.smsContactPurpose.replace(/_/g, ' ')}
                              {request.smsContactPurposeDetails ? ` - ${request.smsContactPurposeDetails}` : ''}
                            </p>
                          ) : null}
                        </div>

                        <div className="grid gap-2 sm:grid-cols-2">
                          <label className="flex items-start gap-2 rounded-xl border border-slate-200/15 bg-slate-900/25 px-3 py-2 text-sm text-slate-200">
                            <input
                              type="checkbox"
                              checked={draft.allowInbound}
                              onChange={(event) => updateDraft(request.id, { ...draft, allowInbound: event.currentTarget.checked })}
                              disabled={busy}
                              className="mt-0.5 h-4 w-4 rounded border-slate-400 bg-slate-950 text-emerald-500 focus:ring-emerald-400"
                            />
                            <span>Allow receiving messages from this contact</span>
                          </label>
                          <label className="flex items-start gap-2 rounded-xl border border-slate-200/15 bg-slate-900/25 px-3 py-2 text-sm text-slate-200">
                            <input
                              type="checkbox"
                              checked={draft.allowOutbound}
                              onChange={(event) => updateDraft(request.id, { ...draft, allowOutbound: event.currentTarget.checked })}
                              disabled={busy}
                              className="mt-0.5 h-4 w-4 rounded border-slate-400 bg-slate-950 text-sky-500 focus:ring-sky-400"
                            />
                            <span>Allow sending messages to this contact</span>
                          </label>
                        </div>

                        {request.channel === 'sms' ? (
                          <label className="flex items-start gap-2 rounded-xl border border-amber-300/20 bg-amber-950/30 px-3 py-2 text-sm text-amber-50">
                            <input
                              type="checkbox"
                              checked={draft.smsContactPermissionAttested}
                              onChange={(event) => updateDraft(request.id, { ...draft, smsContactPermissionAttested: event.currentTarget.checked })}
                              disabled={busy}
                              className="mt-0.5 h-4 w-4 rounded border-amber-200/60 bg-slate-950 text-amber-500 focus:ring-amber-400"
                            />
                            <span>I confirm I have permission to contact this number by SMS.</span>
                          </label>
                        ) : null}
                      </div>
                    </div>
                  </article>
                )
              })}
            </div>
          </>
        )}
      </div>
    </EmbeddedAgentShellPanel>
  )
}
