import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Clock3, Mail, Pencil, RotateCcw, Search, Send, Trash2, X } from 'lucide-react'

import {
  bulkDiscardOutbox,
  decideOutboxItem,
  fetchEmailSendingPolicy,
  fetchOutbox,
  fetchOutboxAgentFiles,
  fetchOutboxItem,
  updateEmailSendingPolicy,
  updateOutboxItem,
  type EmailSendingMode,
  type OutboxItem,
} from '../../api/outbox'
import { HttpError } from '../../api/http'
import { ImmersivePageFrame } from '../../components/common/ImmersivePageFrame'


type ImmersiveOutboxPageProps = {
  layout?: 'main' | 'sidebar-shell'
  refreshKey?: number
}

type OutboxFilter = 'needs_review' | 'sending' | 'failed' | 'recent'

const FILTERS: Array<{ key: OutboxFilter; label: string; countKey: 'needsReview' | 'sending' | 'failed' | 'recent' }> = [
  { key: 'needs_review', label: 'Needs review', countKey: 'needsReview' },
  { key: 'sending', label: 'Sending', countKey: 'sending' },
  { key: 'failed', label: 'Failed', countKey: 'failed' },
  { key: 'recent', label: 'Recent', countKey: 'recent' },
]

const MODE_OPTIONS: Array<{ value: EmailSendingMode; label: string }> = [
  { value: 'review_all_external', label: 'Review before send' },
  { value: 'review_new_contacts', label: 'Review only new contacts' },
  { value: 'send_automatically', label: 'Send automatically' },
]

function errorMessage(error: unknown): string {
  if (error instanceof HttpError && error.body && typeof error.body === 'object') {
    const body = error.body as Record<string, unknown>
    return String(body.message || body.error || 'Unable to update this Outbox item.')
  }
  return error instanceof Error ? error.message : 'Unable to update this Outbox item.'
}

function formatTimestamp(value?: string | null): string {
  if (!value) return ''
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

const EMAIL_PREVIEW_BASE_STYLE = `<style>
  body {
    font-family: 'Inter Variable', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }
</style>`

function buildEmailPreviewDocument(bodyHtml: string): string {
  const headMatch = /<head(?:\s[^>]*)?>/i.exec(bodyHtml)
  if (headMatch?.index !== undefined) {
    const insertionPoint = headMatch.index + headMatch[0].length
    return `${bodyHtml.slice(0, insertionPoint)}${EMAIL_PREVIEW_BASE_STYLE}${bodyHtml.slice(insertionPoint)}`
  }

  const bodyMatch = /<body(?:\s[^>]*)?>/i.exec(bodyHtml)
  if (bodyMatch?.index !== undefined) {
    return `${bodyHtml.slice(0, bodyMatch.index)}${EMAIL_PREVIEW_BASE_STYLE}${bodyHtml.slice(bodyMatch.index)}`
  }

  return `${EMAIL_PREVIEW_BASE_STYLE}${bodyHtml}`
}

function OutboxPolicyPanel({ enabled }: { enabled: boolean }) {
  const queryClient = useQueryClient()
  const policyQuery = useQuery({ queryKey: ['email-sending-policy'], queryFn: fetchEmailSendingPolicy, enabled })
  const mutation = useMutation({
    mutationFn: updateEmailSendingPolicy,
    onSuccess: (data) => queryClient.setQueryData(['email-sending-policy'], data),
  })
  const policy = policyQuery.data
  if (!enabled || !policy) return null
  return (
    <details className="rounded-2xl border border-slate-700/70 bg-slate-950/35 px-4 py-3 text-slate-100">
      <summary className="cursor-pointer text-sm font-semibold">Review Before Send policy</summary>
      <div className="mt-4 grid gap-4 md:grid-cols-3">
        <label className="text-xs font-medium text-slate-300">
          Default for new agents
          <select
            value={policy.defaultMode}
            onChange={(event) => mutation.mutate({ defaultMode: event.target.value })}
            className="mt-2 block w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-white"
          >
            {MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        {policy.canSetMinimum ? (
          <label className="text-xs font-medium text-slate-300">
            Organization minimum
            <select
              value={policy.minimumMode ?? ''}
              onChange={(event) => mutation.mutate({ minimumMode: event.target.value || null })}
              className="mt-2 block w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-white"
            >
              <option value="">No minimum</option>
              {MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
        ) : <div />}
        <label className="flex items-center gap-3 self-end rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200">
          <input
            type="checkbox"
            checked={policy.emailNotificationsEnabled}
            onChange={(event) => mutation.mutate({ emailNotificationsEnabled: event.target.checked })}
            className="size-4 accent-blue-500"
          />
          Email me about pending reviews
        </label>
      </div>
      <button
        type="button"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate({ defaultMode: policy.defaultMode, minimumMode: policy.minimumMode, applyToExisting: true })}
        className="mt-4 rounded-lg border border-blue-400/50 px-3 py-2 text-xs font-semibold text-blue-200 hover:bg-blue-500/10 disabled:opacity-50"
      >
        Apply default to existing agents
      </button>
    </details>
  )
}

function ReviewDetail({ itemId, onClose }: { itemId: string; onClose: () => void }) {
  const queryClient = useQueryClient()
  const detailQuery = useQuery({ queryKey: ['outbox-item', itemId], queryFn: () => fetchOutboxItem(itemId) })
  const item = detailQuery.data
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ subject: '', body: '', attachmentNodeIds: [] as string[] })
  const [attachmentsChanged, setAttachmentsChanged] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [threadAcknowledged, setThreadAcknowledged] = useState(false)

  useEffect(() => {
    if (!item) return
    setDraft({
      subject: item.subject,
      body: item.body || '',
      attachmentNodeIds: item.attachments?.flatMap((attachment) => attachment.nodeId ? [attachment.nodeId] : []) ?? [],
    })
    setAttachmentsChanged(false)
    setThreadAcknowledged(false)
  }, [item])

  const filesQuery = useQuery({
    queryKey: ['outbox-agent-files', item?.agent.id],
    queryFn: () => fetchOutboxAgentFiles(item!.agent.id),
    enabled: Boolean(item && editing),
  })

  const refresh = async (updated: OutboxItem) => {
    queryClient.setQueryData(['outbox-item', itemId], updated)
    await queryClient.invalidateQueries({ queryKey: ['outbox'] })
  }
  const mutation = useMutation({
    mutationFn: async ({ action, saveOnly }: { action?: 'approve' | 'discard' | 'retry'; saveOnly?: boolean }) => {
      if (!item) throw new Error('Outbox item unavailable.')
      const edits: Record<string, unknown> = editing ? {
          subject: draft.subject,
          body: draft.body,
        } : {}
      if (editing && attachmentsChanged) edits.attachmentNodeIds = draft.attachmentNodeIds
      if (saveOnly) return updateOutboxItem(item.id, { expectedVersion: item.version, ...edits })
      if (!action) throw new Error('Missing action.')
      return decideOutboxItem(item.id, action, {
        expectedVersion: item.version,
        acknowledgeThreadChanged: threadAcknowledged,
        ...(action === 'approve' ? edits : {}),
      })
    },
    onSuccess: async (updated) => {
      setError(null)
      setEditing(false)
      await refresh(updated)
    },
    onError: (mutationError) => {
      setError(errorMessage(mutationError))
      if (mutationError instanceof HttpError && mutationError.status === 409) {
        void detailQuery.refetch()
      }
    },
  })

  if (detailQuery.isLoading) return <div className="flex min-h-96 items-center justify-center text-sm text-slate-400">Loading message…</div>
  if (!item) return <div className="p-6 text-sm text-rose-300">{errorMessage(detailQuery.error)}</div>
  const threadChanged = item.warnings.some((warning) => warning.code === 'conversation_changed')
  return (
    <section className="min-h-0 flex-1 overflow-y-auto px-5 py-5 text-slate-100">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-blue-300">{item.agent.name}</p>
          <h2 className="mt-1 text-xl font-semibold">{item.subject || '(No subject)'}</h2>
          <p className="mt-1 text-xs text-slate-400">Queued {formatTimestamp(item.queuedAt)}</p>
        </div>
        <button type="button" onClick={onClose} className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white" aria-label="Close detail"><X className="size-5" /></button>
      </div>
      {item.warnings.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {item.warnings.map((warning) => <span key={warning.code} className="rounded-full border border-amber-400/40 bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-200">{warning.label}</span>)}
        </div>
      ) : null}
      {error ? <p className="mt-4 rounded-lg border border-rose-400/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-100">{error}</p> : null}
      <div className="mt-5 grid gap-3 text-sm">
        <label className="grid gap-1 text-xs text-slate-400">From<input value={item.sender} readOnly className="rounded-lg border border-slate-700 bg-transparent px-3 py-2 text-sm text-slate-200" /></label>
        <label className="grid gap-1 text-xs text-slate-400">To<input value={item.to} readOnly className="rounded-lg border border-slate-700 bg-transparent px-3 py-2 text-sm text-slate-200" /></label>
        <label className="grid gap-1 text-xs text-slate-400">CC<input value={item.cc.join(', ')} readOnly className="rounded-lg border border-slate-700 bg-transparent px-3 py-2 text-sm text-slate-200" /></label>
        <p className="text-xs text-slate-500">Recipients are locked to the email prepared by the agent.</p>
        {editing ? (
          <>
            <label className="grid gap-1 text-xs text-slate-400">Subject<input value={draft.subject} onChange={(event) => setDraft({ ...draft, subject: event.target.value })} className="rounded-lg border border-slate-700 bg-transparent px-3 py-2 text-sm text-slate-100" /></label>
            <label className="grid gap-1 text-xs text-slate-400">Message body<textarea value={draft.body} onChange={(event) => setDraft({ ...draft, body: event.target.value })} rows={14} className="rounded-lg border border-slate-700 bg-transparent px-3 py-2 font-mono text-sm text-slate-100" /></label>
            <fieldset className="rounded-xl border border-slate-700 px-3 py-3">
              <legend className="px-1 text-xs font-medium text-slate-400">Attachments from agent files</legend>
              {filesQuery.isLoading ? <p className="text-xs text-slate-500">Loading files…</p> : null}
              {filesQuery.isError ? <p className="text-xs text-rose-300">Unable to load agent files.</p> : null}
              <div className="max-h-44 space-y-2 overflow-y-auto">
                {filesQuery.data?.map((file) => (
                  <label key={file.id} className="flex items-center gap-2 text-xs text-slate-200">
                    <input
                      type="checkbox"
                      checked={draft.attachmentNodeIds.includes(file.id)}
                      onChange={(event) => {
                        setAttachmentsChanged(true)
                        setDraft((current) => ({
                          ...current,
                          attachmentNodeIds: event.target.checked
                            ? [...current.attachmentNodeIds, file.id]
                            : current.attachmentNodeIds.filter((id) => id !== file.id),
                        }))
                      }}
                      className="size-4 accent-blue-500"
                    />
                    <span className="truncate">{file.path || file.name}</span>
                  </label>
                ))}
                {!filesQuery.isLoading && !filesQuery.data?.length ? <p className="text-xs text-slate-500">This agent has no files available.</p> : null}
              </div>
            </fieldset>
          </>
        ) : (
          <iframe title="Email preview" sandbox="" srcDoc={buildEmailPreviewDocument(item.bodyHtml || '')} className="min-h-96 w-full rounded-xl border border-slate-700 bg-white" />
        )}
      </div>
      {item.attachments?.length ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {item.attachments.map((attachment) => <span key={attachment.id} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300">{attachment.filename}</span>)}
        </div>
      ) : null}
      {threadChanged ? (
        <label className="mt-4 flex items-start gap-2 rounded-lg border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
          <input type="checkbox" checked={threadAcknowledged} onChange={(event) => setThreadAcknowledged(event.target.checked)} className="mt-0.5 size-4 accent-amber-500" />
          I reviewed the newer conversation activity and still want to send this version.
        </label>
      ) : null}
      <div className="mt-5 flex flex-wrap gap-2">
        {item.allowedActions.edit ? <button type="button" onClick={() => setEditing((value) => !value)} className="inline-flex items-center gap-2 rounded-lg border border-slate-600 px-3 py-2 text-sm font-semibold hover:bg-slate-800"><Pencil className="size-4" />{editing ? 'Cancel edit' : 'Edit'}</button> : null}
        {editing ? <button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate({ saveOnly: true })} className="rounded-lg border border-blue-400/50 px-3 py-2 text-sm font-semibold text-blue-200 hover:bg-blue-500/10">Save changes</button> : null}
        {item.allowedActions.approve ? <button type="button" disabled={mutation.isPending || (threadChanged && !threadAcknowledged)} onClick={() => mutation.mutate({ action: 'approve' })} className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-50"><Send className="size-4" />{editing ? 'Save & send' : 'Approve & send'}</button> : null}
        {item.allowedActions.discard ? <button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate({ action: 'discard' })} className="inline-flex items-center gap-2 rounded-lg border border-rose-400/50 px-3 py-2 text-sm font-semibold text-rose-200 hover:bg-rose-500/10"><Trash2 className="size-4" />Discard</button> : null}
        {item.allowedActions.retry ? <button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate({ action: 'retry' })} className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500"><RotateCcw className="size-4" />Retry unchanged email</button> : null}
      </div>
    </section>
  )
}

export function ImmersiveOutboxPage({ layout = 'main', refreshKey = 0 }: ImmersiveOutboxPageProps) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<OutboxFilter>('needs_review')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<Record<string, number>>({})
  const listQuery = useQuery({
    queryKey: ['outbox', filter, search, refreshKey],
    queryFn: () => fetchOutbox(filter, search),
    refetchInterval: 30_000,
  })
  useEffect(() => {
    const refresh = () => void queryClient.invalidateQueries({ queryKey: ['outbox'] })
    window.addEventListener('gobii:outbox-updated', refresh)
    return () => window.removeEventListener('gobii:outbox-updated', refresh)
  }, [queryClient])
  const discardMutation = useMutation({
    mutationFn: () => bulkDiscardOutbox(Object.entries(selected).map(([id, expectedVersion]) => ({ id, expectedVersion }))),
    onSuccess: async () => {
      setSelected({})
      await queryClient.invalidateQueries({ queryKey: ['outbox'] })
    },
  })
  const counts = listQuery.data?.counts
  const items = listQuery.data?.items ?? []
  const available = listQuery.data?.available
  const featureEnabled = listQuery.data?.featureEnabled === true
  const selectedCount = Object.keys(selected).length
  return (
    <ImmersivePageFrame layout={layout} maxWidthClass="max-w-7xl" error={listQuery.error}>
      <div className="space-y-4 text-slate-100">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Review Before Send</p>
            <h1 className="mt-1 text-2xl font-semibold">Outbox</h1>
            <p className="mt-1 text-sm text-slate-400">Nothing leaves Gobii-managed email delivery until an authorized human approves the exact version.</p>
          </div>
          {selectedCount ? <button type="button" onClick={() => discardMutation.mutate()} disabled={discardMutation.isPending} className="rounded-lg border border-rose-400/50 px-3 py-2 text-sm font-semibold text-rose-200">Discard {selectedCount}</button> : null}
        </header>
        <OutboxPolicyPanel enabled={featureEnabled} />
        {available === false ? (
          <div className="rounded-2xl border border-slate-700/70 px-5 py-8 text-center">
            <Mail className="mx-auto size-8 text-slate-500" />
            <p className="mt-3 text-sm font-semibold">Review Before Send is not enabled for this deployment.</p>
          </div>
        ) : null}
        {available !== false ? (
        <div className="overflow-hidden rounded-2xl border border-slate-700/70 bg-slate-950/35">
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
            {FILTERS.map((item) => (
              <button key={item.key} type="button" onClick={() => setFilter(item.key)} className={`rounded-full px-3 py-1.5 text-xs font-semibold ${filter === item.key ? 'bg-blue-600 text-white' : 'border border-slate-700 text-slate-300 hover:bg-slate-800'}`}>
                {item.label} <span className="ml-1 opacity-80">{counts?.[item.countKey] ?? 0}</span>
              </button>
            ))}
            <label className="ml-auto flex min-w-52 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-slate-400">
              <Search className="size-4" />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Recipient or subject" className="w-full bg-transparent text-sm text-white outline-none" />
            </label>
          </div>
          <div className="grid min-h-[34rem] lg:grid-cols-[minmax(19rem,0.8fr)_minmax(0,1.5fr)]">
            <div className={`border-slate-700/70 lg:border-r ${selectedId ? 'hidden lg:block' : ''}`}>
              {listQuery.isLoading ? <p className="p-5 text-sm text-slate-400">Loading Outbox…</p> : null}
              {!listQuery.isLoading && !items.length ? <div className="flex min-h-80 flex-col items-center justify-center px-6 text-center"><Check className="size-8 text-emerald-300" /><p className="mt-3 text-sm font-semibold">Nothing here</p><p className="mt-1 text-xs text-slate-400">This queue is clear.</p></div> : null}
              {items.map((item) => (
                <article key={item.id} className={`flex gap-3 px-4 py-4 transition ${selectedId === item.id ? 'bg-blue-500/10' : 'hover:bg-slate-800/60'}`}>
                  {item.reviewStatus === 'pending' ? <input type="checkbox" checked={item.id in selected} onChange={(event) => setSelected((current) => { const next = { ...current }; if (event.target.checked) next[item.id] = item.version; else delete next[item.id]; return next })} className="mt-1 size-4 accent-blue-500" aria-label={`Select ${item.subject}`} /> : null}
                  <button type="button" onClick={() => setSelectedId(item.id)} className="min-w-0 flex-1 text-left">
                    <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold">{item.subject || '(No subject)'}</span><span className="shrink-0 text-[11px] text-slate-500">{formatTimestamp(item.queuedAt)}</span></div>
                    <p className="mt-1 truncate text-xs text-slate-300">To {item.to}</p>
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{item.bodyPreview}</p>
                    <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-400"><Mail className="size-3" />{item.agent.name}{item.warnings.map((warning) => <span key={warning.code} className="rounded-full border border-amber-400/30 px-1.5 py-0.5 text-amber-200">{warning.label}</span>)}</div>
                  </button>
                </article>
              ))}
            </div>
            {selectedId ? <ReviewDetail itemId={selectedId} onClose={() => setSelectedId(null)} /> : <div className="hidden min-h-96 items-center justify-center text-sm text-slate-500 lg:flex"><div className="text-center"><Clock3 className="mx-auto size-8" /><p className="mt-3">Select an email to review its exact contents.</p></div></div>}
          </div>
        </div>
        ) : null}
      </div>
    </ImmersivePageFrame>
  )
}
