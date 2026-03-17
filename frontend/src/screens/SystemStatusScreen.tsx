import { useQuery } from '@tanstack/react-query'
import { Activity, Loader2, RefreshCw } from 'lucide-react'

import { fetchSystemStatus } from '../api/systemStatus'
import { StatusSections } from '../components/systemStatus/StatusSections'
import { MetricCard, formatDateTime } from '../components/systemStatus/common'
import type { SystemStatusPayload } from '../types/systemStatus'

export function SystemStatusScreen() {
  const query = useQuery({
    queryKey: ['system-status'],
    queryFn: ({ signal }) => fetchSystemStatus(signal),
    refetchOnWindowFocus: false,
    refetchInterval: (queryState) => {
      const payload = queryState.state.data as SystemStatusPayload | undefined
      return (payload?.meta.pollIntervalSeconds ?? 30) * 1000
    },
    placeholderData: (previousData) => previousData,
  })

  const data = query.data

  return (
    <div className="app-shell">
      <main className="app-main">
        <section className="card card--header">
          <div className="card__body card__body--header">
            <div className="app-header">
              <div className="app-badge">
                <Activity className="size-6" />
              </div>
              <div className="flex-1">
                <h1 className="app-title">System Status</h1>
                <p className="app-subtitle">
                  Staff snapshot of queue pressure, active processing, web sessions, sandbox compute, proxy health, and browser task backlog.
                </p>
                <p className="app-context">Environment: {data?.meta.environment || 'Loading'}</p>
              </div>
              <button
                className="inline-flex shrink-0 items-center gap-2 self-start rounded-2xl border border-slate-200/80 bg-white/90 px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-[0_10px_24px_rgba(15,23,42,0.08)] transition hover:border-sky-200 hover:text-sky-700 hover:shadow-[0_14px_28px_rgba(14,165,233,0.14)] disabled:cursor-progress disabled:opacity-60 md:self-center"
                onClick={() => query.refetch()}
                disabled={query.isFetching}
              >
                {query.isFetching ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                {query.isFetching ? 'Refreshing' : 'Refresh'}
              </button>
            </div>

            <dl className="app-meta">
              <div className="app-meta__item">
                <dt>Last refreshed</dt>
                <dd>{data ? formatDateTime(data.meta.refreshedAt) : 'Loading'}</dd>
              </div>
              <div className="app-meta__item">
                <dt>Refresh interval</dt>
                <dd>{data ? `${data.meta.pollIntervalSeconds}s` : '30s'}</dd>
              </div>
              <div className="app-meta__item">
                <dt>Sections</dt>
                <dd>{data ? Object.values(data.sections).filter((section) => section.available).length : 0} available</dd>
              </div>
            </dl>
          </div>
        </section>

        {query.isPending && !data ? (
          <section className="card">
            <div className="status status--loading">
              <p className="status__headline">Loading system snapshot</p>
              <p className="status__details">Pulling Redis, database, and staff status aggregates now.</p>
            </div>
          </section>
        ) : null}

        {query.isError && !data ? (
          <section className="card">
            <div className="status status--error">
              <p className="status__headline">Unable to load system status</p>
              <p className="status__details">
                {query.error instanceof Error ? query.error.message : 'The status API did not return a usable payload.'}
              </p>
            </div>
          </section>
        ) : null}

        {data ? (
          <>
            <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {data.overview.map((card) => (
                <MetricCard key={card.id} card={card} />
              ))}
            </section>

            <section className="grid gap-5">
              <StatusSections data={data} />
            </section>
          </>
        ) : null}

        {query.isFetching && data ? (
          <div className="fixed bottom-6 right-6 inline-flex items-center gap-2 rounded-full bg-slate-950 px-4 py-2 text-sm font-medium text-white shadow-[0_18px_40px_rgba(15,23,42,0.25)]">
            <Loader2 className="size-4 animate-spin" />
            Refreshing snapshot
          </div>
        ) : null}
      </main>
    </div>
  )
}
