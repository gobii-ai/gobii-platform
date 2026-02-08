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
const MAX_AVATAR_LOAD_RETRIES = 3

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
  const [avatarSrc, setAvatarSrc] = useState<string | null>(normalizedAvatarUrl)
  const hasAvatar = Boolean(avatarSrc)
  const [avatarReady, setAvatarReady] = useState(false)
  const [avatarRetryCount, setAvatarRetryCount] = useState(0)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const retryTimeoutRef = useRef<number | null>(null)

  useEffect(() => {
    if (retryTimeoutRef.current !== null) {
      window.clearTimeout(retryTimeoutRef.current)
      retryTimeoutRef.current = null
    }
    setAvatarSrc(normalizedAvatarUrl)
    setAvatarReady(false)
    setAvatarRetryCount(0)
  }, [normalizedAvatarUrl])

  useEffect(() => {
    if (!hasAvatar) {
      return
    }
    const image = imageRef.current
    if (image && image.complete && image.naturalWidth > 0) {
      setAvatarReady(true)
    }
  }, [hasAvatar, avatarSrc])

  useEffect(() => {
    return () => {
      if (retryTimeoutRef.current !== null) {
        window.clearTimeout(retryTimeoutRef.current)
        retryTimeoutRef.current = null
      }
    }
  }, [])

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
          src={avatarSrc ?? undefined}
          alt={`${trimmedName} avatar`}
          className={imageClassName}
          style={imageStyle}
          onLoad={() => {
            setAvatarReady(true)
            setAvatarRetryCount(0)
            if (retryTimeoutRef.current !== null) {
              window.clearTimeout(retryTimeoutRef.current)
              retryTimeoutRef.current = null
            }
          }}
          onError={() => {
            setAvatarReady(false)
            if (!normalizedAvatarUrl || avatarRetryCount >= MAX_AVATAR_LOAD_RETRIES) {
              return
            }
            const nextRetryCount = avatarRetryCount + 1
            setAvatarRetryCount(nextRetryCount)
            const retryDelayMs = Math.min(4000, 750 * 2 ** (nextRetryCount - 1))
            if (retryTimeoutRef.current !== null) {
              window.clearTimeout(retryTimeoutRef.current)
            }
            retryTimeoutRef.current = window.setTimeout(() => {
              const separator = normalizedAvatarUrl.includes('?') ? '&' : '?'
              setAvatarSrc(`${normalizedAvatarUrl}${separator}retry=${Date.now()}`)
            }, retryDelayMs)
          }}
        />
      ) : null}
    </div>
  )
}
