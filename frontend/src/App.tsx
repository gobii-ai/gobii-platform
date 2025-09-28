import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'
import { fetchPing, type PingResponse } from './api/ping'

type Status = 'idle' | 'loading' | 'success' | 'error'

type PingSnapshot = {
  timestamp: number
  payload: PingResponse
}

function formatTimestamp(epochMs: number): string {
  const formatter = new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  return formatter.format(new Date(epochMs))
}

type AppProps = {
  agentId?: string | null
  agentName?: string | null
}

function App({ agentId, agentName }: AppProps) {
  const [status, setStatus] = useState<Status>('idle')
  const [snapshot, setSnapshot] = useState<PingSnapshot | undefined>()
  const [errorMessage, setErrorMessage] = useState<string | undefined>()

  const runPing = useCallback(async () => {
    setStatus('loading')
    setErrorMessage(undefined)

    try {
      const payload = await fetchPing()
      setSnapshot({
        timestamp: Date.now(),
        payload,
      })
      setStatus('success')
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : 'Something went wrong contacting the API.'
      setErrorMessage(message)
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    void runPing()
  }, [runPing])

  const userLabel = useMemo(() => {
    if (!snapshot?.payload.user) {
      return 'anonymous session'
    }
    return snapshot.payload.user
  }, [snapshot])

  const agentLabel = agentName || agentId || 'Persistent agent'

  return (
    <div className="app-shell" data-state={status}>
      <header className="app-header">
        <div className="app-badge">Gobii</div>
        <div>
          <h1 className="app-title">Persistent Agent Chat Shell</h1>
          <p className="app-subtitle">
            This entry point talks to Django through the existing session-powered API.
          </p>
          <p className="app-context">Target: {agentLabel}</p>
        </div>
      </header>

      <main className="app-main">
        <section className="card">
          <header className="card__header">
            <h2 className="card__title">Ping API Status</h2>
            <button
              type="button"
              className="card__cta"
              onClick={() => void runPing()}
              disabled={status === 'loading'}
            >
              {status === 'loading' ? 'Checking…' : 'Run ping'}
            </button>
          </header>

          <div className="card__body">
            {status === 'success' && snapshot ? (
              <div className="status status--success">
                <p className="status__headline">pong ✅</p>
                <dl className="status__details">
                  <div>
                    <dt>Last checked</dt>
                    <dd>{formatTimestamp(snapshot.timestamp)}</dd>
                  </div>
                  {agentId ? (
                    <div>
                      <dt>Agent ID</dt>
                      <dd>{agentId}</dd>
                    </div>
                  ) : null}
                  <div>
                    <dt>Authenticated user</dt>
                    <dd>{userLabel}</dd>
                  </div>
                </dl>
              </div>
            ) : null}

            {status === 'loading' ? (
              <div className="status status--loading">
                <p className="status__headline">Contacting API…</p>
                <p className="status__details">We reuse Django session cookies automatically.</p>
              </div>
            ) : null}

            {status === 'error' ? (
              <div className="status status--error" role="alert">
                <p className="status__headline">Ping failed</p>
                <p className="status__details">{errorMessage}</p>
              </div>
            ) : null}

            {status === 'idle' ? (
              <p className="status__details">Ready when you are.</p>
            ) : null}
          </div>
        </section>

        <section className="card card--secondary">
          <header className="card__header">
            <h2 className="card__title">What&rsquo;s next?</h2>
          </header>
          <div className="card__body">
            <ul className="roadmap">
              <li>Build persistent agent conversation surfaces here.</li>
              <li>Incrementally migrate console UI into this React stack.</li>
              <li>Gradually remove legacy HTMX/Alpine screens.</li>
            </ul>
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
