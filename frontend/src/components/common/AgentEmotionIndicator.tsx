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

  if (!emotionKey || !normalizedEmotion) {
    return null
  }

  return (
    <ActiveAgentEmotionIndicator
      key={emotionKey}
      name={trimmedName}
      emotion={normalizedEmotion}
      expiresAtMs={emotionExpiresAtMs}
      className={className}
    />
  )
}

type ActiveAgentEmotionIndicatorProps = {
  name: string
  emotion: string
  expiresAtMs: number
  className?: string
}

function ActiveAgentEmotionIndicator({
  name,
  emotion,
  expiresAtMs,
  className,
}: ActiveAgentEmotionIndicatorProps) {
  const [active, setActive] = useState(() => expiresAtMs > Date.now())

  useEffect(() => {
    if (!active) {
      return
    }
    const expireIfDue = () => {
      if (expiresAtMs <= Date.now()) {
        setActive(false)
      }
    }
    const timeoutId = window.setTimeout(
      expireIfDue,
      Math.max(0, expiresAtMs - Date.now()),
    )
    window.addEventListener('focus', expireIfDue)
    document.addEventListener('visibilitychange', expireIfDue)
    return () => {
      window.clearTimeout(timeoutId)
      window.removeEventListener('focus', expireIfDue)
      document.removeEventListener('visibilitychange', expireIfDue)
    }
  }, [active, expiresAtMs])

  if (!active) {
    return null
  }

  return (
    <span
      className={['agent-emotion-indicator', className].filter(Boolean).join(' ')}
      data-agent-emotion={emotion}
      role="img"
      aria-label={`${name}'s current emotion: ${emotion}`}
      title={`${name}'s current emotion: ${emotion}`}
    >
      {emotion}
    </span>
  )
}
