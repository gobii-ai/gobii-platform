import { useDeferredValue, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, CheckCircle2, ExternalLink, Loader2, Search, ShieldCheck, UsersRound } from 'lucide-react'

import {
  createStaffUserTaskCreditGrant,
  fetchStaffUserDetail,
  markStaffUserEmailVerified,
  searchStaffUsers,
  type StaffUserDetail,
} from '../api/staffUsers'

export type StaffUsersScreenProps = {
  selectedUserId?: number | null
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

function formatGrantType(value: string): string {
  return value === 'Promo' ? 'Promo' : value === 'Compensation' ? 'Compensation' : value
}

function navigateToUser(userId: number): void {
  window.location.assign(`/staff/users/${userId}/`)
}

function SearchResults({
  query,
  isLoading,
  results,
}: {
  query: string
  isLoading: boolean
  results: Array<{ id: number; name: string; email: string }>
}) {
  if (!query) {
    return null
  }

  return (
    <div className="rounded-2xl border border-sky-100 bg-white p-3 shadow-[0_10px_24px_rgba(15,23,42,0.08)]">
      {isLoading ? (
        <div className="flex items-center gap-2 px-2 py-2 text-sm font-medium text-slate-600">
          <Loader2 className="size-4 animate-spin" />
          Searching users
        </div>
      ) : results.length ? (
        <div className="grid gap-2">
          {results.map((result) => (
            <button
              key={result.id}
              type="button"
              onClick={() => navigateToUser(result.id)}
              className="flex w-full items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 text-left transition hover:border-sky-200 hover:shadow-[0_12px_24px_rgba(14,165,233,0.14)]"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-900">{result.name}</p>
                <p className="truncate text-sm text-slate-600">{result.email || 'No email on file'}</p>
              </div>
              <span className="ml-4 shrink-0 rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700">
                #{result.id}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <div className="px-2 py-2 text-sm text-slate-600">No users matched “{query}”.</div>
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
      <div className="card__header">
        <div>
          <h2 className="card__title">Overview</h2>
          <p className="app-subtitle">Identity, account reference, and fast admin access.</p>
        </div>
        <a
          href={detail.user.adminUrl}
          className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-sky-200 hover:text-sky-700"
        >
          Django Admin
          <ExternalLink className="size-4" />
        </a>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">User ID</p>
          <p className="mt-2 text-lg font-semibold text-slate-900">{detail.user.id}</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">Name</p>
          <p className="mt-2 text-lg font-semibold text-slate-900">{detail.user.name}</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">Email</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <p className="text-lg font-semibold text-slate-900">{detail.user.email || 'No email on file'}</p>
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
        </div>
      </div>
    </section>
  )
}

function BillingCard({ detail }: { detail: StaffUserDetail }) {
  return (
    <section className="card">
      <div className="card__header">
        <div>
          <h2 className="card__title">Billing</h2>
          <p className="app-subtitle">Plan, Stripe customer record, and active personal add-ons.</p>
        </div>
        {detail.billing.stripeCustomerUrl ? (
          <a
            href={detail.billing.stripeCustomerUrl}
            className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:border-sky-200 hover:text-sky-700"
            target="_blank"
            rel="noreferrer"
          >
            View in Stripe
            <ExternalLink className="size-4" />
          </a>
        ) : null}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">Plan</p>
          <p className="mt-2 text-lg font-semibold text-slate-900">{detail.billing.plan.name}</p>
          <p className="mt-1 text-sm text-slate-500">{detail.billing.plan.id}</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">Stripe Customer</p>
          <p className="mt-2 text-lg font-semibold text-slate-900">{detail.billing.stripeCustomerId || 'No Stripe customer'}</p>
        </div>
      </div>

      <div className="grid gap-3">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">Current Add-ons</p>
        {detail.billing.addons.length ? (
          detail.billing.addons.map((addon) => (
            <div key={addon.id} className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
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
            </div>
          ))
        ) : (
          <p className="text-sm text-slate-600">No active personal add-ons.</p>
        )}
      </div>
    </section>
  )
}

function AgentsCard({ detail }: { detail: StaffUserDetail }) {
  return (
    <section className="card">
      <div className="card__header">
        <div>
          <h2 className="card__title">Persistent Agents</h2>
          <p className="app-subtitle">All agents owned by this user, including organization-backed agents.</p>
        </div>
        <span className="app-status-indicator">{detail.agents.length} total</span>
      </div>

      {detail.agents.length ? (
        <div className="grid gap-3">
          {detail.agents.map((agent) => (
            <div key={agent.id} className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">{agent.name || 'Untitled agent'}</p>
                  <p className="mt-1 text-sm text-slate-600">
                    {agent.organizationName ? `Organization: ${agent.organizationName}` : 'Personal agent'}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <a
                    href={agent.auditUrl}
                    className="inline-flex items-center gap-2 rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-800 transition hover:bg-amber-100"
                  >
                    Audit
                  </a>
                  <a
                    href={agent.adminUrl}
                    className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-sky-200 hover:text-sky-700"
                  >
                    Admin
                  </a>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-600">This user does not currently own any persistent agents.</p>
      )}
    </section>
  )
}

function TaskCreditsCard({
  detail,
  onSubmit,
  submitting,
}: {
  detail: StaffUserDetail
  onSubmit: (payload: { credits: string; grantType: 'Compensation' | 'Promo'; expirationPreset: 'one_month' | 'one_year' }) => void
  submitting: boolean
}) {
  const [credits, setCredits] = useState('25')
  const [grantType, setGrantType] = useState<'Compensation' | 'Promo'>('Compensation')
  const [expirationPreset, setExpirationPreset] = useState<'one_month' | 'one_year'>('one_month')

  return (
    <section className="card">
      <div className="card__header">
        <div>
          <h2 className="card__title">Task Credits</h2>
          <p className="app-subtitle">Personal balance, recent grants, and a fast manual grant form.</p>
        </div>
        <span className="app-status-indicator app-status-indicator--success">
          {detail.taskCredits.unlimited ? 'Unlimited' : `${detail.taskCredits.available ?? 0} available`}
        </span>
      </div>

      <div className="grid gap-3">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">Recent Grants</p>
        {detail.taskCredits.recentGrants.length ? (
          detail.taskCredits.recentGrants.map((grant) => (
            <div key={grant.id} className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">
                  {grant.credits} credits · {formatGrantType(grant.grantType)}
                </p>
                <p className="text-xs font-medium text-slate-500">{grant.available} remaining in block</p>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Granted {formatDateTime(grant.grantedAt)} · Expires {formatDateTime(grant.expiresAt)}
              </p>
              {grant.comments ? <p className="mt-2 text-sm text-slate-600">{grant.comments}</p> : null}
            </div>
          ))
        ) : (
          <p className="text-sm text-slate-600">No personal task-credit grants found.</p>
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

export function StaffUsersScreen({ selectedUserId = null }: StaffUsersScreenProps) {
  const queryClient = useQueryClient()
  const [searchInput, setSearchInput] = useState('')
  const [feedback, setFeedback] = useState<string | null>(null)
  const deferredSearchInput = useDeferredValue(searchInput.trim())

  const searchQuery = useQuery({
    queryKey: ['staff-user-search', deferredSearchInput],
    queryFn: ({ signal }) => searchStaffUsers(deferredSearchInput, 8, signal),
    enabled: deferredSearchInput.length > 0,
    placeholderData: (previousData) => previousData,
  })

  const detailQuery = useQuery({
    queryKey: ['staff-user-detail', selectedUserId],
    queryFn: ({ signal }) => fetchStaffUserDetail(selectedUserId as number, signal),
    enabled: selectedUserId !== null,
  })

  const verifyMutation = useMutation({
    mutationFn: () => markStaffUserEmailVerified(selectedUserId as number),
    onSuccess: async () => {
      setFeedback('Email marked verified.')
      await queryClient.invalidateQueries({ queryKey: ['staff-user-detail', selectedUserId] })
    },
  })

  const grantMutation = useMutation({
    mutationFn: (payload: { credits: string; grantType: 'Compensation' | 'Promo'; expirationPreset: 'one_month' | 'one_year' }) =>
      createStaffUserTaskCreditGrant(selectedUserId as number, payload),
    onSuccess: async () => {
      setFeedback('Task-credit grant created.')
      await queryClient.invalidateQueries({ queryKey: ['staff-user-detail', selectedUserId] })
    },
  })

  const detail = detailQuery.data
  const searchResults = searchQuery.data?.users ?? []
  const searchError = searchQuery.error instanceof Error ? searchQuery.error.message : null
  const detailError = detailQuery.error instanceof Error ? detailQuery.error.message : null

  const pageSubtitle = !detail
    ? 'Search by name or email to jump directly into a user’s billing, verification, agents, and task credits.'
    : `Viewing ${detail.user.name} · ${detail.user.email || `User #${detail.user.id}`}`

  const handleSearchSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const exactResult = searchResults.find((user) => user.email.toLowerCase() === searchInput.trim().toLowerCase())
    const fallback = searchResults[0]
    if (exactResult) {
      navigateToUser(exactResult.id)
      return
    }
    if (fallback) {
      navigateToUser(fallback.id)
      return
    }
    if (/^\d+$/.test(searchInput.trim())) {
      navigateToUser(Number(searchInput.trim()))
    }
  }

  const handleGrantSubmit = (payload: { credits: string; grantType: 'Compensation' | 'Promo'; expirationPreset: 'one_month' | 'one_year' }) => {
    grantMutation.mutate(payload)
  }

  const handleVerify = () => {
    if (selectedUserId === null) {
      return
    }
    verifyMutation.mutate()
  }

  return (
    <div className="app-shell">
      <main className="app-main">
        <section className="card card--header">
          <div className="card__body card__body--header">
            <div className="app-header">
              <div className="app-badge">
                <UsersRound className="size-6" />
              </div>
              <div className="flex-1">
                <h1 className="app-title">Users</h1>
                <p className="app-subtitle">{pageSubtitle}</p>
                <p className="app-context">Staff tools for fast account triage and user switching.</p>
              </div>
              {detail ? (
                <a
                  href={detail.user.adminUrl}
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
                  placeholder="Search users by name, email, or user ID"
                  className="w-full rounded-[1.75rem] border border-slate-200 bg-white px-12 py-4 text-sm text-slate-900 outline-none transition focus:border-sky-400"
                  autoComplete="off"
                />
              </label>
              <SearchResults query={deferredSearchInput} isLoading={searchQuery.isFetching} results={searchResults} />
              {searchError ? <p className="text-sm text-rose-700">{searchError}</p> : null}
              {feedback ? <p className="text-sm font-medium text-emerald-700">{feedback}</p> : null}
              {verifyMutation.error instanceof Error ? <p className="text-sm text-rose-700">{verifyMutation.error.message}</p> : null}
              {grantMutation.error instanceof Error ? <p className="text-sm text-rose-700">{grantMutation.error.message}</p> : null}
            </form>
          </div>
        </section>

        {selectedUserId === null ? (
          <section className="card">
            <div className="status status--loading">
              <p className="status__headline">Select a user to inspect</p>
              <p className="status__details">Use the search box above to jump between users without leaving staff tooling.</p>
            </div>
          </section>
        ) : null}

        {detailQuery.isPending && selectedUserId !== null ? (
          <section className="card">
            <div className="status status--loading">
              <p className="status__headline">Loading user details</p>
              <p className="status__details">Pulling verification, billing, agents, and task credits now.</p>
            </div>
          </section>
        ) : null}

        {detailError && !detail ? (
          <section className="card">
            <div className="status status--error">
              <p className="status__headline">Unable to load this user</p>
              <p className="status__details">{detailError}</p>
            </div>
          </section>
        ) : null}

        {detail ? (
          <>
            <OverviewCard detail={detail} isVerifying={verifyMutation.isPending} onVerify={handleVerify} />
            <BillingCard detail={detail} />
            <AgentsCard detail={detail} />
            <TaskCreditsCard detail={detail} onSubmit={handleGrantSubmit} submitting={grantMutation.isPending} />
          </>
        ) : null}
      </main>
    </div>
  )
}
