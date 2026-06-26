import type { ButtonHTMLAttributes, ElementType, HTMLAttributes, ReactNode } from 'react'

export type AgentChatTone = 'neutral' | 'info' | 'success' | 'warning' | 'critical'

export function joinClassNames(...classNames: Array<string | false | null | undefined>) {
  return classNames.filter(Boolean).join(' ')
}

type PolymorphicSurfaceProps = {
  as?: ElementType
  tone?: AgentChatTone
  variant?: 'solid' | 'subtle' | 'glass'
  className?: string
  children: ReactNode
} & HTMLAttributes<HTMLElement>

export function AgentChatSurface({
  as: Component = 'div',
  tone = 'neutral',
  variant = 'subtle',
  className,
  children,
  ...rest
}: PolymorphicSurfaceProps) {
  return (
    <Component
      className={joinClassNames('agent-chat-surface', className)}
      data-tone={tone}
      data-variant={variant}
      {...rest}
    >
      {children}
    </Component>
  )
}

type SectionCardProps = {
  tone?: AgentChatTone
  density?: 'compact' | 'normal'
  className?: string
  children: ReactNode
} & HTMLAttributes<HTMLElement>

export function AgentChatSectionCard({
  tone = 'neutral',
  density = 'normal',
  className,
  children,
  ...rest
}: SectionCardProps) {
  return (
    <section
      className={joinClassNames('agent-chat-section-card', className)}
      data-tone={tone}
      data-density={density}
      {...rest}
    >
      {children}
    </section>
  )
}

type StatusBadgeProps = {
  tone?: AgentChatTone
  className?: string
  children: ReactNode
} & HTMLAttributes<HTMLSpanElement>

export function AgentChatStatusBadge({
  tone = 'neutral',
  className,
  children,
  ...rest
}: StatusBadgeProps) {
  return (
    <span className={joinClassNames('agent-chat-status-badge', className)} data-tone={tone} {...rest}>
      {children}
    </span>
  )
}

type PillProps = {
  tone?: AgentChatTone
  className?: string
  children: ReactNode
} & HTMLAttributes<HTMLSpanElement>

export function AgentChatPill({
  tone = 'neutral',
  className,
  children,
  ...rest
}: PillProps) {
  return (
    <span className={joinClassNames('agent-chat-pill', className)} data-tone={tone} {...rest}>
      {children}
    </span>
  )
}

type IconButtonProps = {
  tone?: AgentChatTone
  size?: 'sm' | 'md'
} & ButtonHTMLAttributes<HTMLButtonElement>

export function AgentChatIconButton({
  tone = 'neutral',
  size = 'md',
  className,
  children,
  ...rest
}: IconButtonProps) {
  return (
    <button
      type="button"
      className={joinClassNames('agent-chat-icon-button', className)}
      data-tone={tone}
      data-size={size}
      {...rest}
    >
      {children}
    </button>
  )
}
