import { useEffect, useState } from 'react'

type AgentEmotionIndicatorProps = {
  name: string
  emotion?: string | null
  emotionExpiresAt?: string | null
  className?: string
}

export function AgentEmotionIndicator({
  name,
  emotion,
  emotionExpiresAt,
  className,
}: AgentEmotionIndicatorProps) {
  const trimmedName = name.trim() || 'Agent'
  const normalizedEmotion = (emotion || '').trim() || null
  const emotionExpiresAtMs = emotionExpiresAt ? Date.parse(emotionExpiresAt) : Number.NaN
  const emotionKey = normalizedEmotion && Number.isFinite(emotionExpiresAtMs)
    ? `${normalizedEmotion}\u0000${emotionExpiresAtMs}`
    : null
  const [visibleEmotionKey, setVisibleEmotionKey] = useState<string | null>(() => (
    emotionKey && emotionExpiresAtMs > Date.now() ? emotionKey : null
  ))

  useEffect(() => {
    const syncVisibility = () => {
      setVisibleEmotionKey(
        emotionKey && emotionExpiresAtMs > Date.now() ? emotionKey : null,
      )
    }
    const syncTimeoutId = window.setTimeout(syncVisibility, 0)
    const expiryTimeoutId = emotionKey
      ? window.setTimeout(syncVisibility, Math.max(0, emotionExpiresAtMs - Date.now()))
      : null
    window.addEventListener('focus', syncVisibility)
    document.addEventListener('visibilitychange', syncVisibility)
    return () => {
      window.clearTimeout(syncTimeoutId)
      if (expiryTimeoutId !== null) {
        window.clearTimeout(expiryTimeoutId)
      }
      window.removeEventListener('focus', syncVisibility)
      document.removeEventListener('visibilitychange', syncVisibility)
    }
  }, [emotionExpiresAtMs, emotionKey])

  if (!normalizedEmotion || visibleEmotionKey !== emotionKey) {
    return null
  }

  return (
    <span
      className={['agent-emotion-indicator', className].filter(Boolean).join(' ')}
      data-agent-emotion={normalizedEmotion}
      role="img"
      aria-label={`${trimmedName}'s current emotion: ${normalizedEmotion}`}
      title={`${trimmedName}'s current emotion: ${normalizedEmotion}`}
    >
      {normalizedEmotion}
    </span>
  )
}
