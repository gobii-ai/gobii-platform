import { useEffect, useRef, useState, type CSSProperties } from 'react'

type AgentAvatarBadgeProps = {
  name: string
  avatarUrl?: string | null
  className?: string
  imageClassName?: string
  textClassName?: string
  style?: CSSProperties
  fallbackStyle?: CSSProperties
}

const AVATAR_FADE_MS = 260

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
  const normalizedAvatarUrl = (avatarUrl || '').trim() || null
  const hasAvatar = Boolean(normalizedAvatarUrl)
  const [avatarReady, setAvatarReady] = useState(false)
  const imageRef = useRef<HTMLImageElement | null>(null)

  useEffect(() => {
    setAvatarReady(false)
  }, [normalizedAvatarUrl])

  useEffect(() => {
    if (!hasAvatar) {
      return
    }
    const image = imageRef.current
    if (image && image.complete && image.naturalWidth > 0) {
      setAvatarReady(true)
    }
  }, [hasAvatar, normalizedAvatarUrl])

  const containerStyle: CSSProperties = {
    position: 'relative',
    overflow: 'hidden',
    ...style,
  }

  const fallbackStyleWithFade: CSSProperties = {
    position: 'absolute',
    inset: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    opacity: hasAvatar ? (avatarReady ? 0 : 1) : 1,
    transition: `opacity ${AVATAR_FADE_MS}ms ease`,
    ...fallbackStyle,
  }

  const imageStyle: CSSProperties = {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    opacity: hasAvatar && avatarReady ? 1 : 0,
    transition: `opacity ${AVATAR_FADE_MS}ms ease`,
  }

  return (
    <div className={className} style={containerStyle}>
      <span className={textClassName} style={fallbackStyleWithFade}>
        {initials || 'A'}
      </span>
      {hasAvatar ? (
        <img
          ref={imageRef}
          src={normalizedAvatarUrl ?? undefined}
          alt={`${trimmedName} avatar`}
          className={imageClassName}
          style={imageStyle}
          onLoad={() => setAvatarReady(true)}
          onError={() => setAvatarReady(false)}
        />
      ) : null}
    </div>
  )
}
