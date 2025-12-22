import type { CSSProperties } from 'react'

type AgentAvatarBadgeProps = {
  name: string
  avatarUrl?: string | null
  className?: string
  imageClassName?: string
  textClassName?: string
  style?: CSSProperties
  fallbackStyle?: CSSProperties
}

export function AgentAvatarBadge({
  name,
  avatarUrl,
  className,
  imageClassName,
  textClassName,
  style,
  fallbackStyle,
}: AgentAvatarBadgeProps) {
  const trimmedName = name.trim() || 'Agent'
  const nameParts = trimmedName.split(/\s+/).filter(Boolean)
  const firstInitial = nameParts[0]?.charAt(0).toUpperCase() || 'A'
  const lastInitial = nameParts.length > 1 ? nameParts[nameParts.length - 1]?.charAt(0).toUpperCase() || '' : ''
  const initials = `${firstInitial}${lastInitial}`.trim()
  const hasAvatar = Boolean(avatarUrl)

  return (
    <div className={className} style={style}>
      {hasAvatar ? (
        <img src={avatarUrl ?? undefined} alt={`${trimmedName} avatar`} className={imageClassName} />
      ) : (
        <span className={textClassName} style={fallbackStyle}>
          {initials || 'A'}
        </span>
      )}
    </div>
  )
}
