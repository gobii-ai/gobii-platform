import { AlertTriangle, ExternalLink, X } from 'lucide-react'

export type HighPriorityBannerTone = 'warning' | 'critical'

export type HighPriorityBannerConfig = {
  id: string
  title: string
  message: string
  actionLabel?: string
  actionHref?: string
  dismissible?: boolean
  tone?: HighPriorityBannerTone
}

type HighPriorityBannerProps = {
  title: string
  message: string
  actionLabel?: string
  actionHref?: string
  dismissible?: boolean
  tone?: HighPriorityBannerTone
  onDismiss?: () => void
}

export function HighPriorityBanner({
  title,
  message,
  actionLabel,
  actionHref,
  dismissible = true,
  tone = 'warning',
  onDismiss,
}: HighPriorityBannerProps) {
  const className = `high-priority-banner high-priority-banner--${tone}`

  return (
    <section className={className} role="alert" aria-live="assertive">
      <div className="high-priority-banner-main">
        <span className="high-priority-banner-icon" aria-hidden="true">
          <AlertTriangle size={16} />
        </span>
        <div className="high-priority-banner-copy">
          <p className="high-priority-banner-title">{title}</p>
          <p className="high-priority-banner-message">{message}</p>
        </div>
      </div>
      <div className="high-priority-banner-actions">
        {actionHref && actionLabel ? (
          <a href={actionHref} target="_top" className="high-priority-banner-link" rel="noreferrer">
            <span>{actionLabel}</span>
            <ExternalLink size={13} />
          </a>
        ) : null}
        {dismissible && onDismiss ? (
          <button
            type="button"
            className="high-priority-banner-dismiss"
            onClick={onDismiss}
            aria-label="Dismiss notification"
          >
            <X size={16} />
          </button>
        ) : null}
      </div>
    </section>
  )
}
