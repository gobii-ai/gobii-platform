import { useMemo } from 'react'

import { PingStatusCard, type PingDetail } from '../components/PingStatusCard'
import { usePingProbe } from '../hooks/usePingProbe'

type AgentChatShellScreenProps = {
  agentId?: string | null
  agentName?: string | null
}

export function AgentChatShellScreen({ agentId, agentName }: AgentChatShellScreenProps) {
  const { status, snapshot, errorMessage, runPing } = usePingProbe()

  const userLabel = useMemo(() => {
    if (!snapshot?.payload.user) {
      return 'anonymous session'
    }

    return snapshot.payload.user
  }, [snapshot])

  const details = useMemo<PingDetail[]>(() => {
    const items: PingDetail[] = []

    if (agentId) {
      items.push({ label: 'Agent ID', value: agentId })
    }

    items.push({ label: 'Authenticated user', value: userLabel })

    return items
  }, [agentId, userLabel])

  const agentLabel = agentName || agentId || 'Persistent agent'

  return (
    <div className="app-shell" data-state={status}>
      <header className="app-header">
        <div className="app-badge">Gobii</div>
        <div>
          <h1 className="app-title">Chat - {agentLabel}</h1>
          <p className="app-subtitle">
            This entry point talks to Django through the existing session-powered API.
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
        />

        <section className="card card--secondary">
          <header className="card__header">
            <h2 className="card__title">What's next?</h2>
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
