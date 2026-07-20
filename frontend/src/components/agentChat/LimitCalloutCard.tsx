import { useCallback, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { AlertTriangle, X } from 'lucide-react'

import { ensureAuthenticated, subscriptionActions, type UpgradeModalSource } from '../../store/subscriptionSlice'
import { useAppDispatch } from '../../store/hooks'
import { AgentChatSectionCard } from './uiPrimitives'

type LimitCalloutCardProps = {
  title: ReactNode
  subtitle: ReactNode
  tone?: 'warning' | 'critical'
  className?: string
  billingIssue?: string
  onDismiss?: () => void
  dismissLabel?: string
  contentActions?: ReactNode
  children?: ReactNode
}

export function LimitCalloutCard({
  title,
  subtitle,
  tone = 'warning',
  className = '',
  billingIssue,
  onDismiss,
  dismissLabel = 'Dismiss warning',
  contentActions,
  children,
}: LimitCalloutCardProps) {
  return (
    <AgentChatSectionCard
      className={`timeline-event hard-limit-callout ${className}`.trim()}
      tone={tone}
      data-billing-issue={billingIssue}
    >
      {onDismiss ? (
        <button type="button" className="hard-limit-callout-dismiss" onClick={onDismiss} aria-label={dismissLabel}>
          <X size={16} />
        </button>
      ) : null}
      <div className="hard-limit-callout-header">
        <span className="hard-limit-callout-icon" aria-hidden="true">
          <AlertTriangle size={16} />
        </span>
        <div className="hard-limit-callout-content">
          <p className="hard-limit-callout-title">{title}</p>
          <p className="hard-limit-callout-subtitle">{subtitle}</p>
          {contentActions}
        </div>
      </div>
      {children}
    </AgentChatSectionCard>
  )
}

export function LimitCalloutActions({ children }: { children: ReactNode }) {
  return <div className="hard-limit-callout-actions">{children}</div>
}

export function LimitCalloutButton({
  variant,
  children,
  className = '',
  ...props
}: {
  variant?: 'secondary' | 'upgrade' | 'addons' | 'purchase'
  children: ReactNode
  className?: string
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={`hard-limit-callout-button${variant ? ` hard-limit-callout-button--${variant}` : ''} ${className}`.trim()}
      {...props}
    >
      {children}
    </button>
  )
}

export function useAuthenticatedUpgrade(source: UpgradeModalSource) {
  const dispatch = useAppDispatch()
  return useCallback(async () => {
    const authenticated = await dispatch(ensureAuthenticated()).unwrap()
    if (authenticated) dispatch(subscriptionActions.openUpgradeModal({ source }))
  }, [dispatch, source])
}
