import { formatTimeOfDay } from '../utils/datetime'
import type { PingStatus } from '../hooks/usePingProbe'

export type ProbeSnapshot = {
  timestamp: number
}

type ExtendedSnapshot = ProbeSnapshot & Record<string, unknown>

export type PingDetail = {
  label: string
  value: string
}

export type PingStatusCopy = {
  successHeadline?: string
  loadingHeadline?: string
  loadingDetails?: string
  errorHeadline?: string
  idleMessage?: string
}

type PingStatusCardProps = {
  title: string
  status: PingStatus
  snapshot?: ExtendedSnapshot
  errorMessage?: string
  onRunPing: () => void | Promise<void>
  details?: PingDetail[]
  copy?: PingStatusCopy
}

const DEFAULT_COPY: Required<PingStatusCopy> = {
  successHeadline: 'pong (success)',
  loadingHeadline: 'Contacting API...',
  loadingDetails: 'We reuse Django session cookies automatically.',
  errorHeadline: 'Ping failed',
  idleMessage: 'Ready when you are.',
}

function normalizeCopy(copy?: PingStatusCopy): Required<PingStatusCopy> {
  if (!copy) {
    return DEFAULT_COPY
  }

  return {
    successHeadline: copy.successHeadline ?? DEFAULT_COPY.successHeadline,
    loadingHeadline: copy.loadingHeadline ?? DEFAULT_COPY.loadingHeadline,
    loadingDetails: copy.loadingDetails ?? DEFAULT_COPY.loadingDetails,
    errorHeadline: copy.errorHeadline ?? DEFAULT_COPY.errorHeadline,
    idleMessage: copy.idleMessage ?? DEFAULT_COPY.idleMessage,
  }
}

export function PingStatusCard({
  title,
  status,
  snapshot,
  errorMessage,
  onRunPing,
  details = [],
  copy,
}: PingStatusCardProps) {
  const resolvedCopy = normalizeCopy(copy)

  return (
    <section className="card" data-section="ping-status">
      <header className="card__header">
        <h2 className="card__title">{title}</h2>
        <button
          type="button"
          className="card__cta"
          onClick={() => void onRunPing()}
          disabled={status === 'loading'}
        >
          {status === 'loading' ? 'Checking...' : 'Run ping'}
        </button>
      </header>

      <div className="card__body">
        {status === 'success' && snapshot ? (
          <div className="status status--success">
            <p className="status__headline">{resolvedCopy.successHeadline}</p>
            <dl className="status__details">
              <div>
                <dt>Last checked</dt>
                <dd>{formatTimeOfDay(snapshot.timestamp)}</dd>
              </div>
              {details.map((detail) => (
                <div key={detail.label}>
                  <dt>{detail.label}</dt>
                  <dd>{detail.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}

        {status === 'loading' ? (
          <div className="status status--loading">
            <p className="status__headline">{resolvedCopy.loadingHeadline}</p>
            <p className="status__details">{resolvedCopy.loadingDetails}</p>
          </div>
        ) : null}

        {status === 'error' ? (
          <div className="status status--error" role="alert">
            <p className="status__headline">{resolvedCopy.errorHeadline}</p>
            <p className="status__details">{errorMessage}</p>
          </div>
        ) : null}

        {status === 'idle' ? (
          <p className="status__details">{resolvedCopy.idleMessage}</p>
        ) : null}
      </div>
    </section>
  )
}
