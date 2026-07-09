import { useDeferredValue, useState, type FormEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, Building2, CheckCircle2, Clock3, ExternalLink, Loader2, Mail, MessageSquare, PlayCircle, Search, Send, ShieldCheck, UsersRound } from 'lucide-react'

import { ActionConfirmDialog } from '../components/common/ActionConfirmDialog'
import { ModalForm } from '../components/common/ModalForm'
import {
  createStaffOrgSystemMessage, createStaffOrgTaskCreditGrant, createStaffUserSystemMessage, createStaffUserTaskCreditGrant, fetchStaffOrgDetail, fetchStaffUserDetail, markStaffUserEmailVerified,
  searchStaffUsers, sendStaffUserEmailTrigger, triggerStaffOrgProcessEvents, triggerStaffUserProcessEvents, type StaffAgentSummary, type StaffOrgDetail, type StaffScopedSystemMessagePayload,
  type StaffTaskCredits, type StaffTaskCreditGrantPayload, type StaffUserDetail, type StaffUserEmailTrigger,
} from '../api/staffUsers'

export type StaffUsersScreenProps = {
  selectedUserId?: number | null
  selectedOrgId?: string | null
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return 'Not set'
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  } catch {
    return value
  }
}

function navigateToUser(userId: number): void {
  window.location.assign(`/staff/users/${userId}/`)
}

function navigateToOrg(orgId: string): void {
  window.location.assign(`/staff/orgs/${orgId}/`)
}

type DetailField = {
  label: string
  value: ReactNode
  description?: ReactNode
}

function AdminLink({ href, label = 'Django Admin', target, compact = false }: { href: string; label?: string; target?: string; compact?: boolean }) {
  return (
    <a
      href={href}
      className={`inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white text-sm font-semibold text-slate-700 transition hover:border-sky-200 hover:text-sky-700 ${compact ? 'px-3 py-2' : 'px-4 py-2'}`}
      target={target}
      rel={target === '_blank' ? 'noreferrer' : undefined}
    >
      {label}
      {compact ? null : <ExternalLink className="size-4" />}
    </a>
  )
}

function CardHeader({ title, subtitle, action, status }: { title: string; subtitle: string; action?: ReactNode; status?: ReactNode }) {
  return (
    <div className="card__header">
      <div>
        <h2 className="card__title">{title}</h2>
        <p className="app-subtitle">{subtitle}</p>
      </div>
      {action ?? status}
    </div>
  )
}

function FieldGrid({ items, columns = 'md:grid-cols-3' }: { items: DetailField[]; columns?: string }) {
  return (
    <div className={`grid gap-4 ${columns}`}>
      {items.map((item) => (
        <div key={item.label}>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">{item.label}</p>
          <div className="mt-2 text-lg font-semibold text-slate-900">{item.value}</div>
          {item.description ? <p className="mt-1 text-sm text-slate-500">{item.description}</p> : null}
        </div>
      ))}
    </div>
  )
}

function RowCard({ children }: { children: ReactNode }) {
  return <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">{children}</div>
}

function SearchResultGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="grid gap-2">
      <p className="px-2 text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">{title}</p>
      {children}
    </div>
  )
}

function SearchResultButton({ title, subtitle, badge, onClick }: { title: string; subtitle: string; badge: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 text-left transition hover:border-sky-200 hover:shadow-[0_12px_24px_rgba(14,165,233,0.14)]"
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-slate-900">{title}</p>
        <p className="truncate text-sm text-slate-600">{subtitle}</p>
      </div>
      <span className="ml-4 shrink-0 rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700">
        {badge}
      </span>
    </button>
  )
}

function SearchResults({
  query,
  isLoading,
  users,
  organizations,
}: {
  query: string
  isLoading: boolean
  users: Array<{ id: number; name: string; email: string }>
  organizations: Array<{ id: string; name: string; slug: string }>
}) {
  if (!query) {
    return null
  }

  const hasResults = users.length > 0 || organizations.length > 0

  return (
    <div className="rounded-2xl border border-sky-100 bg-white p-3 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
      {isLoading ? (
        <div className="flex items-center gap-2 px-2 py-2 text-sm font-medium text-slate-600">
          <Loader2 className="size-4 animate-spin" />
          Searching users and orgs
        </div>
      ) : hasResults ? (
        <div className="grid gap-2">
          {users.length ? (
            <SearchResultGroup title="Users">
              {users.map((result) => (
                <SearchResultButton
                  key={result.id}
                  title={result.name}
                  subtitle={result.email || 'No email on file'}
                  badge={`#${result.id}`}
                  onClick={() => navigateToUser(result.id)}
                />
              ))}
            </SearchResultGroup>
          ) : null}
          {organizations.length ? (
            <SearchResultGroup title="Organizations">
              {organizations.map((result) => (
                <SearchResultButton
                  key={result.id}
                  title={result.name}
                  subtitle={result.slug}
                  badge="Org"
                  onClick={() => navigateToOrg(result.id)}
                />
              ))}
            </SearchResultGroup>
          ) : null}
        </div>
      ) : (
        <div className="px-2 py-2 text-sm text-slate-600">No users or orgs matched “{query}”.</div>
      )}
    </div>
  )
}

function OverviewCard({
  detail,
  isVerifying,
  onVerify,
}: {
  detail: StaffUserDetail
  isVerifying: boolean
  onVerify: () => void
}) {
  const verified = detail.emailVerification.isVerified

  return (
    <section className="card">
      <CardHeader
        title="Overview"
        subtitle="Identity, account reference, and fast admin access."
        action={<AdminLink href={detail.user.adminUrl} />}
      />
      <FieldGrid
        items={[
          { label: 'User ID', value: detail.user.id },
          { label: 'Name', value: detail.user.name },
          {
            label: 'Email',
            value: (
              <div className="flex flex-wrap items-center gap-2">
                <span>{detail.user.email || 'No email on file'}</span>
                {detail.emailVerification.email ? (
                  <span className={`app-status-indicator ${verified ? 'app-status-indicator--success' : 'app-status-indicator--error'}`}>
                    {verified ? 'Verified' : 'Unverified'}
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={onVerify}
                  disabled={verified || isVerifying || !detail.emailVerification.email}
                  className="inline-flex items-center justify-center gap-2 rounded-2xl bg-sky-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isVerifying ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
                  Mark Verified
                </button>
              </div>
            ),
          },
        ]}
      />
    </section>
  )
}

function BillingCard({ detail }: { detail: StaffUserDetail }) {
  return (
    <section className="card">
      <CardHeader
        title="Billing"
        subtitle="Plan, Stripe customer record, and active personal add-ons."
        action={
          detail.billing.stripeCustomerUrl ? (
            <AdminLink href={detail.billing.stripeCustomerUrl} label="View in Stripe" target="_blank" />
          ) : null
        }
      />
      <FieldGrid
        columns="md:grid-cols-2"
        items={[
          { label: 'Plan', value: detail.billing.plan.name, description: detail.billing.plan.id },
          { label: 'Stripe Customer', value: detail.billing.stripeCustomerId || 'No Stripe customer' },
        ]}
      />

      <div className="grid gap-3">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">Current Add-ons</p>
        {detail.billing.addons.length ? (
          detail.billing.addons.map((addon) => (
            <RowCard key={addon.id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">{addon.label}</p>
                <span className="rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700">
                  Qty {addon.quantity}
                </span>
              </div>
              <p className="mt-2 text-sm text-slate-600">{addon.summary}</p>
              <p className="mt-1 text-xs text-slate-500">
                Starts {formatDateTime(addon.startsAt)} · Expires {formatDateTime(addon.expiresAt)}
              </p>
            </RowCard>
          ))
        ) : (
          <p className="text-sm text-slate-600">No active personal add-ons.</p>
        )}
      </div>
    </section>
  )
}

function formatAgentTarget(count: number, singularLabel: string): string {
  return `${count} ${count === 1 ? singularLabel : `${singularLabel}s`}`
}

function SystemMessageModal({
  targetLabel,
  targetCount,
  body,
  submitting,
  error,
  onBodyChange,
  onClose,
  onSubmit,
}: {
  targetLabel: string
  targetCount: number
  body: string
  submitting: boolean
  error?: string | null
  onBodyChange: (body: string) => void
  onClose: () => void
  onSubmit: () => void
}) {
  return (
    <ModalForm
      id="staff-scoped-system-message-form"
      title="System Message"
      subtitle={`Queue a pending directive for ${formatAgentTarget(targetCount, targetLabel)}.`}
      icon={MessageSquare}
      iconBgClass="bg-sky-100"
      iconColorClass="text-sky-700"
      widthClass="sm:max-w-2xl"
      onClose={onClose}
      dismissible={!submitting}
      submitLabel="Queue Message"
      submittingLabel="Queueing..."
      submitting={submitting}
      submitDisabled={!body.trim()}
      errorMessages={error ? [error] : null}
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <label className="grid gap-2 text-sm font-semibold text-slate-700">
        Directive
        <textarea
          value={body}
          onChange={(event) => onBodyChange(event.currentTarget.value)}
          rows={6}
          className="resize-y rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-normal text-slate-900 outline-none transition focus:border-sky-400"
          placeholder="Enter the directive agents should see the next time they process events."
        />
      </label>
    </ModalForm>
  )
}

function ProcessEventsModal({
  targetLabel,
  targetCount,
  submitting,
  error,
  onClose,
  onConfirm,
}: {
  targetLabel: string
  targetCount: number
  submitting: boolean
  error?: string | null
  onClose: () => void
  onConfirm: () => void
}) {
  return (
    <ActionConfirmDialog
      open
      title="Process Events"
      description={`This will queue event processing for up to ${formatAgentTarget(targetCount, targetLabel)}. This can be expensive for large agent sets.`}
      confirmLabel="Queue Processing"
      busy={submitting}
      localError={error}
      icon={AlertTriangle}
      onClose={onClose}
      onConfirm={onConfirm}
    />
  )
}

function AgentsCard({
  agents,
  title = 'Persistent Agents',
  subtitle,
  emptyText,
  actionTargetLabel,
  actionTargetCount,
  onSystemMessage,
  onProcessEvents,
  systemMessagePending = false,
  processEventsPending = false,
}: {
  agents: StaffAgentSummary[]
  title?: string
  subtitle: string
  emptyText: string
  actionTargetLabel: string
  actionTargetCount: number
  onSystemMessage: () => void
  onProcessEvents: () => void
  systemMessagePending?: boolean
  processEventsPending?: boolean
}) {
  const actionsDisabled = actionTargetCount === 0

  return (
    <section className="card">
      <CardHeader
        title={title}
        subtitle={subtitle}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <span className="app-status-indicator">{agents.length} total</span>
            <button
              type="button"
              onClick={onSystemMessage}
              disabled={actionsDisabled || systemMessagePending || processEventsPending}
              className="inline-flex items-center gap-2 rounded-2xl border border-sky-200 bg-white px-3 py-2 text-sm font-semibold text-sky-700 transition hover:border-sky-300 hover:text-sky-800 disabled:cursor-not-allowed disabled:opacity-60"
              title={actionsDisabled ? `No ${actionTargetLabel}s available` : `Queue a system message for ${formatAgentTarget(actionTargetCount, actionTargetLabel)}`}
            >
              {systemMessagePending ? <Loader2 className="size-4 animate-spin" /> : <MessageSquare className="size-4" />}
              System Message
            </button>
            <button
              type="button"
              onClick={onProcessEvents}
              disabled={actionsDisabled || systemMessagePending || processEventsPending}
              className="inline-flex items-center gap-2 rounded-2xl border border-amber-200 bg-white px-3 py-2 text-sm font-semibold text-amber-800 transition hover:border-amber-300 hover:text-amber-900 disabled:cursor-not-allowed disabled:opacity-60"
              title={actionsDisabled ? `No ${actionTargetLabel}s available` : `Queue processing for ${formatAgentTarget(actionTargetCount, actionTargetLabel)}`}
            >
              {processEventsPending ? <Loader2 className="size-4 animate-spin" /> : <PlayCircle className="size-4" />}
              Process Events
            </button>
          </div>
        }
      />

      {agents.length ? (
        <div className="grid gap-3">
          {agents.map((agent) => (
            <RowCard key={agent.id}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{agent.name || 'Untitled agent'}</p>
                  <p className="mt-1 text-sm text-slate-600">
                    {agent.organizationName ? `Organization: ${agent.organizationName}` : 'Personal agent'}
                  </p>
                  <p className="mt-1 flex items-center gap-1.5 text-xs font-medium text-slate-500">
                    <Clock3 className="size-3.5" />
                    Last interaction: {formatDateTime(agent.lastInteractionAt)}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <a
                    href={agent.auditUrl}
                    className="inline-flex items-center gap-2 rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-800 transition hover:bg-amber-100"
                  >
                    Audit
                  </a>
                  <AdminLink href={agent.adminUrl} label="Admin" compact />
                </div>
              </div>
            </RowCard>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-600">{emptyText}</p>
      )}
    </section>
  )
}

function OrganizationOverviewCard({ detail }: { detail: StaffOrgDetail }) {
  return (
    <section className="card">
      <CardHeader
        title="Overview"
        subtitle="Organization identity, status, and admin reference."
        action={<AdminLink href={detail.organization.adminUrl} />}
      />
      <FieldGrid
        columns="md:grid-cols-4"
        items={[
          { label: 'Organization', value: detail.organization.name, description: detail.organization.slug },
          { label: 'Status', value: detail.organization.isActive ? 'Active' : 'Inactive' },
          { label: 'Plan', value: detail.billing.subscription || detail.organization.plan },
          { label: 'Created', value: formatDateTime(detail.organization.createdAt) },
        ]}
      />
      <FieldGrid
        items={[
          { label: 'Purchased Seats', value: detail.billing.purchasedSeats ?? 'Not set' },
          { label: 'Reserved Seats', value: detail.billing.seatsReserved ?? 'Not set' },
          { label: 'Available Seats', value: detail.billing.seatsAvailable ?? 'Not set' },
        ]}
      />
    </section>
  )
}

function OrganizationMembersCard({ detail }: { detail: StaffOrgDetail }) {
  return (
    <section className="card">
      <CardHeader
        title="Members"
        subtitle="Active members currently attached to this organization."
        status={<span className="app-status-indicator">{detail.members.length} active</span>}
      />

      {detail.members.length ? (
        <div className="grid gap-3">
          {detail.members.map((member) => (
            <RowCard key={member.userId}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-slate-900">{member.name}</p>
                  <p className="mt-1 truncate text-sm text-slate-600">{member.email || 'No email on file'}</p>
                  <p className="mt-1 text-xs font-medium text-slate-500">{member.roleLabel}</p>
                </div>
                <AdminLink href={member.adminUrl} label="Admin" compact />
              </div>
            </RowCard>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-600">This organization does not currently have active members.</p>
      )}
    </section>
  )
}

function UserEmailsCard({
  detail,
  sendingTriggerId,
  onSend,
}: {
  detail: StaffUserDetail
  sendingTriggerId: number | null
  onSend: (trigger: StaffUserEmailTrigger) => void
}) {
  return (
    <section className="card">
      <CardHeader
        title="User Emails"
        subtitle="Send configured Customer.io launch events through Analytics."
        status={<span className="app-status-indicator">{detail.userEmails.triggers.length} active</span>}
      />

      {detail.userEmails.triggers.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {detail.userEmails.triggers.map((trigger) => {
            const isSending = sendingTriggerId === trigger.id
            return (
              <RowCard key={trigger.id}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-slate-900">{trigger.name}</p>
                    <p className="mt-1 truncate text-xs text-slate-500">{trigger.eventName}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => onSend(trigger)}
                    disabled={sendingTriggerId !== null}
                    className="inline-flex items-center gap-2 rounded-2xl bg-sky-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {isSending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                    Send
                  </button>
                </div>
              </RowCard>
            )
          })}
        </div>
      ) : (
        <div className="flex items-center gap-3 text-sm text-slate-600">
          <Mail className="size-4 text-slate-400" />
          No active user email triggers are configured.
        </div>
      )}
    </section>
  )
}

function TaskCreditsCard({
  taskCredits,
  onSubmit,
  submitting,
  subtitle,
}: {
  taskCredits: StaffTaskCredits
  onSubmit: (payload: StaffTaskCreditGrantPayload) => void
  submitting: boolean
  subtitle: string
}) {
  const [credits, setCredits] = useState('25')
  const [grantType, setGrantType] = useState<'Compensation' | 'Promo'>('Compensation')
  const [expirationPreset, setExpirationPreset] = useState<'one_month' | 'one_year'>('one_month')

  return (
    <section className="card">
      <CardHeader
        title="Task Credits"
        subtitle={subtitle}
        status={
          <span className="app-status-indicator app-status-indicator--success">
            {taskCredits.unlimited ? 'Unlimited' : `${taskCredits.available ?? 0} available`}
          </span>
        }
      />

      <div className="grid gap-3">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">Current Grants</p>
        {taskCredits.recentGrants.length ? (
          taskCredits.recentGrants.map((grant) => (
            <RowCard key={grant.id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">
                  {grant.credits} credits · {grant.grantType}
                </p>
                <p className="text-xs font-medium text-slate-500">{grant.available} remaining in block</p>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Granted {formatDateTime(grant.grantedAt)} · Expires {formatDateTime(grant.expiresAt)}
              </p>
              {grant.comments ? <p className="mt-2 text-sm text-slate-600">{grant.comments}</p> : null}
            </RowCard>
          ))
        ) : (
          <p className="text-sm text-slate-600">No current task-credit grants found.</p>
        )}
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit({ credits, grantType, expirationPreset })
        }}
        className="grid gap-4 rounded-3xl border border-sky-100 bg-white p-5"
      >
        <div className="grid gap-4 md:grid-cols-3">
          <label className="grid gap-2 text-sm font-semibold text-slate-700">
            Credits
            <input
              name="credits"
              type="number"
              min="0.001"
              step="0.001"
              value={credits}
              onChange={(event) => setCredits(event.currentTarget.value)}
              className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-sky-400"
            />
          </label>
          <label className="grid gap-2 text-sm font-semibold text-slate-700">
            Grant Type
            <select
              name="grantType"
              value={grantType}
              onChange={(event) => setGrantType(event.currentTarget.value as 'Compensation' | 'Promo')}
              className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-sky-400"
            >
              <option value="Compensation">Compensation</option>
              <option value="Promo">Promo</option>
            </select>
          </label>
          <label className="grid gap-2 text-sm font-semibold text-slate-700">
            Expiration
            <select
              name="expirationPreset"
              value={expirationPreset}
              onChange={(event) => setExpirationPreset(event.currentTarget.value as 'one_month' | 'one_year')}
              className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-sky-400"
            >
              <option value="one_month">1 month</option>
              <option value="one_year">1 year</option>
            </select>
          </label>
        </div>
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-2xl bg-sky-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}
            Add Grant
          </button>
        </div>
      </form>
    </section>
  )
}

export function StaffUsersScreen({ selectedUserId = null, selectedOrgId = null }: StaffUsersScreenProps) {
  const queryClient = useQueryClient()
  const [searchInput, setSearchInput] = useState('')
  const [feedback, setFeedback] = useState<string | null>(null)
  const [sendingEmailTriggerId, setSendingEmailTriggerId] = useState<number | null>(null)
  const [activeAgentAction, setActiveAgentAction] = useState<'system-message' | 'process-events' | null>(null)
  const [systemMessageBody, setSystemMessageBody] = useState('')
  const deferredSearchInput = useDeferredValue(searchInput.trim())
  const hasSelectedUser = selectedUserId !== null
  const hasSelectedOrg = Boolean(selectedOrgId)

  const searchQuery = useQuery({
    queryKey: ['staff-user-search', deferredSearchInput],
    queryFn: ({ signal }) => searchStaffUsers(deferredSearchInput, 8, signal),
    enabled: deferredSearchInput.length > 0,
    placeholderData: (previousData) => previousData,
  })

  const detailQuery = useQuery({
    queryKey: ['staff-user-detail', selectedUserId],
    queryFn: ({ signal }) => fetchStaffUserDetail(selectedUserId as number, signal),
    enabled: hasSelectedUser,
  })

  const orgDetailQuery = useQuery({
    queryKey: ['staff-org-detail', selectedOrgId],
    queryFn: ({ signal }) => fetchStaffOrgDetail(selectedOrgId as string, signal),
    enabled: hasSelectedOrg,
  })

  const verifyMutation = useMutation({
    mutationFn: () => markStaffUserEmailVerified(selectedUserId as number),
    onSuccess: async () => {
      setFeedback('Email marked verified.')
      await queryClient.invalidateQueries({ queryKey: ['staff-user-detail', selectedUserId] })
    },
  })

  const grantMutation = useMutation({
    mutationFn: (payload: StaffTaskCreditGrantPayload) => createStaffUserTaskCreditGrant(selectedUserId as number, payload),
    onSuccess: async () => {
      setFeedback('Task-credit grant created.')
      await queryClient.invalidateQueries({ queryKey: ['staff-user-detail', selectedUserId] })
    },
  })

  const orgGrantMutation = useMutation({
    mutationFn: (payload: StaffTaskCreditGrantPayload) => createStaffOrgTaskCreditGrant(selectedOrgId as string, payload),
    onSuccess: async () => {
      setFeedback('Organization task-credit grant created.')
      await queryClient.invalidateQueries({ queryKey: ['staff-org-detail', selectedOrgId] })
    },
  })

  const emailTriggerMutation = useMutation({
    mutationFn: (trigger: StaffUserEmailTrigger) => {
      setSendingEmailTriggerId(trigger.id)
      return sendStaffUserEmailTrigger(selectedUserId as number, trigger.id)
    },
    onSuccess: async (payload) => {
      setFeedback(`Sent ${payload.userEmail.name}.`)
      await queryClient.invalidateQueries({ queryKey: ['staff-user-detail', selectedUserId] })
    },
    onSettled: () => {
      setSendingEmailTriggerId(null)
    },
  })

  const systemMessageMutation = useMutation({
    mutationFn: (payload: StaffScopedSystemMessagePayload) => {
      if (selectedOrgId) {
        return createStaffOrgSystemMessage(selectedOrgId, payload)
      }
      if (selectedUserId !== null) {
        return createStaffUserSystemMessage(selectedUserId, payload)
      }
      throw new Error('Select a user or organization first.')
    },
    onSuccess: (payload) => {
      setFeedback(`System message queued for ${payload.createdCount} agent${payload.createdCount === 1 ? '' : 's'}.`)
      setSystemMessageBody('')
      setActiveAgentAction(null)
    },
  })

  const processEventsMutation = useMutation({
    mutationFn: () => {
      if (selectedOrgId) {
        return triggerStaffOrgProcessEvents(selectedOrgId)
      }
      if (selectedUserId !== null) {
        return triggerStaffUserProcessEvents(selectedUserId)
      }
      throw new Error('Select a user or organization first.')
    },
    onSuccess: (payload) => {
      const skipped = payload.skippedInactiveCount ? ` ${payload.skippedInactiveCount} inactive skipped.` : ''
      setFeedback(`Process events queued for ${payload.queuedCount} agent${payload.queuedCount === 1 ? '' : 's'}.${skipped}`)
      setActiveAgentAction(null)
    },
  })

  const userDetail = detailQuery.data
  const orgDetail = orgDetailQuery.data
  const adminUrl = userDetail?.user.adminUrl ?? orgDetail?.organization.adminUrl
  const searchUsers = searchQuery.data?.users ?? []
  const searchOrganizations = searchQuery.data?.organizations ?? []
  const searchError = searchQuery.error instanceof Error ? searchQuery.error.message : null
  const userDetailError = detailQuery.error instanceof Error ? detailQuery.error.message : null
  const orgDetailError = orgDetailQuery.error instanceof Error ? orgDetailQuery.error.message : null
  const detailError = userDetailError || orgDetailError
  const personalAgentTargetCount = userDetail?.agents.filter((agent) => !agent.organizationName).length ?? 0
  const actionTargetCount = orgDetail ? orgDetail.agents.length : personalAgentTargetCount
  const actionTargetLabel = orgDetail ? 'organization agent' : 'personal agent'
  const agentActionPending = systemMessageMutation.isPending || processEventsMutation.isPending
  const systemMessageError = systemMessageMutation.error instanceof Error ? systemMessageMutation.error.message : null
  const processEventsError = processEventsMutation.error instanceof Error ? processEventsMutation.error.message : null

  const pageSubtitle = userDetail
    ? `Viewing ${userDetail.user.name} · ${userDetail.user.email || `User #${userDetail.user.id}`}`
    : orgDetail
      ? `Viewing ${orgDetail.organization.name} · ${orgDetail.organization.slug}`
      : 'Search by name, email, user ID, org name, slug, or org ID to jump into staff triage.'

  const handleSearchSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const query = searchInput.trim()
    const normalizedQuery = query.toLowerCase()
    const exactUser = searchUsers.find((user) => user.email.toLowerCase() === normalizedQuery)
    const exactOrg = searchOrganizations.find((org) => org.slug.toLowerCase() === normalizedQuery || org.id.toLowerCase() === normalizedQuery)
    if (exactUser) {
      navigateToUser(exactUser.id)
      return
    }
    if (exactOrg) {
      navigateToOrg(exactOrg.id)
      return
    }
    if (searchUsers[0]) {
      navigateToUser(searchUsers[0].id)
      return
    }
    if (searchOrganizations[0]) {
      navigateToOrg(searchOrganizations[0].id)
      return
    }
    if (/^\d+$/.test(query)) {
      navigateToUser(Number(query))
    }
  }

  const handleVerify = () => {
    if (selectedUserId === null) {
      return
    }
    verifyMutation.mutate()
  }

  const handleEmailSend = (trigger: StaffUserEmailTrigger) => {
    if (selectedUserId === null || !userDetail) {
      return
    }
    const target = userDetail.user.email || `user #${userDetail.user.id}`
    const confirmed = window.confirm(`Send "${trigger.name}" to ${target}?\n\nThis will emit "${trigger.eventName}" through Analytics.`)
    if (!confirmed) {
      return
    }
    emailTriggerMutation.mutate(trigger)
  }

  const closeAgentActionModal = () => {
    if (agentActionPending) {
      return
    }
    systemMessageMutation.reset()
    processEventsMutation.reset()
    setActiveAgentAction(null)
  }

  const openSystemMessageModal = () => {
    systemMessageMutation.reset()
    setActiveAgentAction('system-message')
  }

  const openProcessEventsModal = () => {
    processEventsMutation.reset()
    setActiveAgentAction('process-events')
  }

  const handleSystemMessageSubmit = () => {
    const body = systemMessageBody.trim()
    if (!body) {
      return
    }
    systemMessageMutation.mutate({ body })
  }

  const handleProcessEventsConfirm = () => {
    processEventsMutation.mutate()
  }

  return (
    <div className="app-shell">
      <main className="app-main">
        <section className="card card--header">
          <div className="card__body card__body--header">
            <div className="app-header">
              <div className="app-badge">
                {orgDetail ? <Building2 className="size-6" /> : <UsersRound className="size-6" />}
              </div>
              <div className="flex-1">
                <h1 className="app-title">Users & Orgs</h1>
                <p className="app-subtitle">{pageSubtitle}</p>
                <p className="app-context">Staff tools for fast account and organization triage.</p>
              </div>
              {adminUrl ? (
                <a
                  href={adminUrl}
                  className="inline-flex shrink-0 items-center gap-2 self-start rounded-2xl border border-slate-200/80 bg-white/90 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-sky-200 hover:text-sky-700 md:self-center"
                >
                  <Activity className="size-4" />
                  Open Admin
                </a>
              ) : null}
            </div>

            <form onSubmit={handleSearchSubmit} className="grid gap-3">
              <label className="relative block">
                <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                <input
                  type="search"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.currentTarget.value)}
                  placeholder="Search users and orgs by name, email, slug, or ID"
                  className="w-full rounded-[1.75rem] border border-slate-200 bg-white px-12 py-4 text-sm text-slate-900 outline-none transition focus:border-sky-400"
                  autoComplete="off"
                />
              </label>
              <SearchResults
                query={deferredSearchInput}
                isLoading={searchQuery.isFetching}
                users={searchUsers}
                organizations={searchOrganizations}
              />
              {searchError ? <p className="text-sm text-rose-700">{searchError}</p> : null}
              {feedback ? <p className="text-sm font-medium text-emerald-700">{feedback}</p> : null}
              {verifyMutation.error instanceof Error ? <p className="text-sm text-rose-700">{verifyMutation.error.message}</p> : null}
              {grantMutation.error instanceof Error ? <p className="text-sm text-rose-700">{grantMutation.error.message}</p> : null}
              {orgGrantMutation.error instanceof Error ? <p className="text-sm text-rose-700">{orgGrantMutation.error.message}</p> : null}
              {emailTriggerMutation.error instanceof Error ? <p className="text-sm text-rose-700">{emailTriggerMutation.error.message}</p> : null}
            </form>
          </div>
        </section>

        {!hasSelectedUser && !hasSelectedOrg ? (
          <section className="card">
            <div className="status status--loading">
              <p className="status__headline">Select a user or org to inspect</p>
              <p className="status__details">Use the search box above to jump between accounts and organizations without leaving staff tooling.</p>
            </div>
          </section>
        ) : null}

        {detailQuery.isPending && hasSelectedUser ? (
          <section className="card">
            <div className="status status--loading">
              <p className="status__headline">Loading user details</p>
              <p className="status__details">Pulling verification, billing, agents, and task credits now.</p>
            </div>
          </section>
        ) : null}

        {orgDetailQuery.isPending && hasSelectedOrg ? (
          <section className="card">
            <div className="status status--loading">
              <p className="status__headline">Loading organization details</p>
              <p className="status__details">Pulling overview, members, agents, and seat information now.</p>
            </div>
          </section>
        ) : null}

        {detailError && !userDetail && !orgDetail ? (
          <section className="card">
            <div className="status status--error">
              <p className="status__headline">Unable to load this selection</p>
              <p className="status__details">{detailError}</p>
            </div>
          </section>
        ) : null}

        {userDetail ? (
          <>
            <OverviewCard detail={userDetail} isVerifying={verifyMutation.isPending} onVerify={handleVerify} />
            <BillingCard detail={userDetail} />
            <AgentsCard
              agents={userDetail.agents}
              subtitle="All agents owned by this user, including organization-backed agents."
              emptyText="This user does not currently own any persistent agents."
              actionTargetLabel="personal agent"
              actionTargetCount={personalAgentTargetCount}
              onSystemMessage={openSystemMessageModal}
              onProcessEvents={openProcessEventsModal}
              systemMessagePending={systemMessageMutation.isPending}
              processEventsPending={processEventsMutation.isPending}
            />
            <UserEmailsCard detail={userDetail} sendingTriggerId={sendingEmailTriggerId} onSend={handleEmailSend} />
            <TaskCreditsCard
              taskCredits={userDetail.taskCredits}
              subtitle="Personal balance, current grants, and a fast manual grant form."
              onSubmit={(payload) => grantMutation.mutate(payload)}
              submitting={grantMutation.isPending}
            />
          </>
        ) : null}

        {orgDetail ? (
          <>
            <OrganizationOverviewCard detail={orgDetail} />
            <OrganizationMembersCard detail={orgDetail} />
            <AgentsCard
              agents={orgDetail.agents}
              subtitle="All persistent agents assigned to this organization."
              emptyText="This organization does not currently own any persistent agents."
              actionTargetLabel="organization agent"
              actionTargetCount={orgDetail.agents.length}
              onSystemMessage={openSystemMessageModal}
              onProcessEvents={openProcessEventsModal}
              systemMessagePending={systemMessageMutation.isPending}
              processEventsPending={processEventsMutation.isPending}
            />
            <TaskCreditsCard
              taskCredits={orgDetail.taskCredits}
              subtitle="Organization balance, current grants, and a fast manual grant form."
              onSubmit={(payload) => orgGrantMutation.mutate(payload)}
              submitting={orgGrantMutation.isPending}
            />
          </>
        ) : null}
      </main>
      {activeAgentAction === 'system-message' ? (
        <SystemMessageModal
          targetLabel={actionTargetLabel}
          targetCount={actionTargetCount}
          body={systemMessageBody}
          submitting={systemMessageMutation.isPending}
          error={systemMessageError}
          onBodyChange={setSystemMessageBody}
          onClose={closeAgentActionModal}
          onSubmit={handleSystemMessageSubmit}
        />
      ) : null}
      {activeAgentAction === 'process-events' ? (
        <ProcessEventsModal
          targetLabel={actionTargetLabel}
          targetCount={actionTargetCount}
          submitting={processEventsMutation.isPending}
          error={processEventsError}
          onClose={closeAgentActionModal}
          onConfirm={handleProcessEventsConfirm}
        />
      ) : null}
    </div>
  )
}
