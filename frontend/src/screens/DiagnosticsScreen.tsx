import { useMemo } from 'react'

import { PingStatusCard, type PingDetail } from '../components/PingStatusCard'
import { usePingProbe } from '../hooks/usePingProbe'
import { formatTimeOfDay } from '../utils/datetime'

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

  const statusLabel = useMemo(() => {
    switch (status) {
      case 'success':
        return 'Connected'
      case 'error':
        return 'Error'
      case 'loading':
        return 'Checking'
      default:
        return 'Idle'
    }
  }, [status])

  const lastChecked = useMemo(() => {
    if (!snapshot) {
      return 'Not checked yet'
    }

    return formatTimeOfDay(snapshot.timestamp)
  }, [snapshot])

  return (
    <div className="app-shell" data-state={status}>
      <main className="app-main">
        <section className="card card--header" data-section="diagnostics-overview">
          <div className="card__body card__body--header">
            <div className="app-header">
              <div>
                <h1 className="app-title">Diagnostics</h1>
                <p className="app-subtitle">
                  System status and connectivity checks for the React console shell.
                </p>
              </div>
            </div>

            <dl className="app-meta">
              <div className="app-meta__item">
                <dt>Status</dt>
                <dd className={`app-status-indicator app-status-indicator--${status}`}>
                  {statusLabel}
                </dd>
              </div>
              <div className="app-meta__item">
                <dt>Last checked</dt>
                <dd>{lastChecked}</dd>
              </div>
              <div className="app-meta__item">
                <dt>Authenticated user</dt>
                <dd>{userLabel}</dd>
              </div>
            </dl>
          </div>
        </section>

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
      </main>
    </div>
  )
}
