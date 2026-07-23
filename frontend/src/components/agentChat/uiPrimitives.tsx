import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ElementType, HTMLAttributes, ReactNode } from 'react'
import type { CSSProperties } from 'react'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'

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
  & ButtonHTMLAttributes<HTMLButtonElement>
  & AnchorHTMLAttributes<HTMLAnchorElement>

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

type ButtonProps = {
  as?: ElementType
  tone?: AgentChatTone
  variant?: 'ghost' | 'soft' | 'solid'
  size?: 'sm' | 'md'
} & ButtonHTMLAttributes<HTMLButtonElement> & AnchorHTMLAttributes<HTMLAnchorElement>

export function AgentChatButton({
  as: Component = 'button',
  tone = 'neutral',
  variant = 'soft',
  size = 'md',
  className,
  children,
  type,
  ...rest
}: ButtonProps) {
  return (
    <Component
      type={Component === 'button' ? (type ?? 'button') : type}
      className={joinClassNames('agent-chat-button', className)}
      data-tone={tone}
      data-variant={variant}
      data-size={size}
      {...rest}
    >
      {children}
    </Component>
  )
}

type MenuItemProps = {
  as?: ElementType
  className?: string
  children: ReactNode
} & ButtonHTMLAttributes<HTMLButtonElement> & HTMLAttributes<HTMLAnchorElement>
  & AnchorHTMLAttributes<HTMLAnchorElement>

export function AgentChatMenuItem({
  as: Component = 'button',
  className,
  children,
  type,
  ...rest
}: MenuItemProps) {
  return (
    <Component
      type={Component === 'button' ? (type ?? 'button') : type}
      className={joinClassNames('agent-chat-menu-item', className)}
      {...rest}
    >
      {children}
    </Component>
  )
}

type AvatarProps = {
  name: string
  avatarUrl?: string | null
  className?: string
  imageClassName?: string
  textClassName?: string
  fallbackStyle?: CSSProperties
}

export function AgentChatAvatar({
  name,
  avatarUrl,
  className,
  imageClassName,
  textClassName,
  fallbackStyle,
}: AvatarProps) {
  return (
    <AgentAvatarBadge
      name={name}
      avatarUrl={avatarUrl}
      className={joinClassNames('agent-chat-avatar', className)}
      imageClassName={joinClassNames('agent-chat-avatar__image', imageClassName)}
      textClassName={joinClassNames('agent-chat-avatar__text', textClassName)}
      fallbackStyle={fallbackStyle}
    />
  )
}
