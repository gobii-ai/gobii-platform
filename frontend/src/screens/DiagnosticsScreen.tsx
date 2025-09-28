import { useMemo } from 'react'

import { PingStatusCard, type PingDetail } from '../components/PingStatusCard'
import { usePingProbe } from '../hooks/usePingProbe'

export function DiagnosticsScreen() {
  const { status, snapshot, errorMessage, runPing } = usePingProbe()

  const userLabel = useMemo(() => {
    if (!snapshot?.payload.user) {
      return 'anonymous session'
    }

    return snapshot.payload.user
  }, [snapshot])

  const details = useMemo<PingDetail[]>(() => {
    if (!snapshot) {
      return []
    }

    return [{ label: 'Authenticated user', value: userLabel }]
  }, [snapshot, userLabel])

  return (
    <div className="app-shell" data-state={status}>
      <header className="app-header">
        <div className="app-badge">Gobii</div>
        <div>
          <h1 className="app-title">Console Diagnostics</h1>
          <p className="app-subtitle">
            Quick verification that the React bundle can mount and reach Django APIs.
          </p>
        </div>
      </header>

      <main className="app-main">
        <PingStatusCard
          title="Ping API Status"
          status={status}
          snapshot={snapshot}
          errorMessage={errorMessage}
          onRunPing={runPing}
          details={details}
          copy={{
            successHeadline: 'React bundle is live (success)',
            loadingDetails: 'Use this to confirm console React wiring and API connectivity.',
          }}
        />

        <section className="card card--secondary">
          <header className="card__header">
            <h2 className="card__title">Integration Checklist</h2>
          </header>
          <div className="card__body">
            <ul className="roadmap">
              <li>React builds load through Vite and mount into the console shell.</li>
              <li>Session-authenticated API calls succeed via `fetchPing`.</li>
              <li>Add additional diagnostics cards here as more surface areas migrate.</li>
            </ul>
          </div>
        </section>
      </main>
    </div>
  )
}
