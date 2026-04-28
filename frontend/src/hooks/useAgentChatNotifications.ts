import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { ConsoleContext } from '../api/context'
import type { AgentMessageNotification } from '../types/agentChat'

type NotificationPermissionState = NotificationPermission | 'unsupported'

export type AgentChatNotificationStatus = 'off' | 'on' | 'needs_permission' | 'blocked'

type AgentChatNotificationOptions = {
  enabled: boolean
  currentContext: ConsoleContext | null
  activeAgentId: string | null
  availableAgentIds?: string[]
  onOpenAgent: (agentId: string) => void
}

type AudioContextConstructor = typeof AudioContext

function getAudioContextConstructor(): AudioContextConstructor | null {
  if (typeof window === 'undefined') {
    return null
  }
  const candidate = (window as Window & { webkitAudioContext?: AudioContextConstructor }).AudioContext
    ?? (window as Window & { webkitAudioContext?: AudioContextConstructor }).webkitAudioContext
  return candidate ?? null
}

export function nativeNotificationsSupported(): boolean {
  if (typeof window === 'undefined') {
    return false
  }
  return 'Notification' in window && window.isSecureContext !== false
}

export function readAgentChatNotificationPermission(): NotificationPermissionState {
  if (!nativeNotificationsSupported()) {
    return 'unsupported'
  }
  return Notification.permission
}

export function resolveAgentChatNotificationStatus(
  enabled: boolean,
  permission: NotificationPermissionState,
): AgentChatNotificationStatus {
  if (!enabled) {
    return 'off'
  }
  if (permission === 'granted') {
    return 'on'
  }
  if (permission === 'default') {
    return 'needs_permission'
  }
  return 'blocked'
}

function isForegroundPage(): boolean {
  if (typeof document === 'undefined') {
    return false
  }
  const visible = document.visibilityState === 'visible'
  const focused = typeof document.hasFocus === 'function' ? document.hasFocus() : true
  return visible && focused
}

function trimNotificationText(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function shouldDispatchAgentChatNotification({
  event,
  currentContext,
  activeAgentId,
  availableAgentIds,
}: {
  event: AgentMessageNotification
  currentContext: ConsoleContext | null
  activeAgentId: string | null
  availableAgentIds?: readonly string[]
}): boolean {
  if (!currentContext) {
    return false
  }
  const knownAgentIds = new Set(availableAgentIds ?? [])
  if (event.agent_id !== activeAgentId && !knownAgentIds.has(event.agent_id)) {
    return false
  }
  if (event.agent_id !== activeAgentId) {
    return true
  }
  return !isForegroundPage()
}

function buildNotificationTitle(event: AgentMessageNotification): string {
  return `New message from ${event.agent_name || 'Agent'}`
}

function buildNotificationBody(event: AgentMessageNotification): string {
  return trimNotificationText(event.message.body_preview) || 'Open chat to view the latest message.'
}

export function useAgentChatNotifications({
  enabled,
  currentContext,
  activeAgentId,
  availableAgentIds = [],
  onOpenAgent,
}: AgentChatNotificationOptions) {
  const [notificationPermission, setNotificationPermission] = useState<NotificationPermissionState>(() =>
    readAgentChatNotificationPermission(),
  )
  const audioContextRef = useRef<AudioContext | null>(null)
  const openAgentRef = useRef(onOpenAgent)
  const seenMessageIdsRef = useRef<Set<string>>(new Set())
  const liveNotificationsRef = useRef<Map<string, Notification>>(new Map())

  useEffect(() => {
    openAgentRef.current = onOpenAgent
  }, [onOpenAgent])

  const refreshNotificationPermission = useCallback(() => {
    const next = readAgentChatNotificationPermission()
    setNotificationPermission(next)
    return next
  }, [])

  const ensureAudioContext = useCallback(async () => {
    const AudioContextImpl = getAudioContextConstructor()
    if (!AudioContextImpl) {
      return null
    }
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContextImpl()
    }
    if (audioContextRef.current.state === 'suspended') {
      try {
        await audioContextRef.current.resume()
      } catch {
        return audioContextRef.current
      }
    }
    return audioContextRef.current
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const refresh = () => {
      refreshNotificationPermission()
    }
    const unlockAudio = () => {
      void ensureAudioContext()
    }

    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('pointerdown', unlockAudio, { passive: true })
    window.addEventListener('keydown', unlockAudio)

    return () => {
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('pointerdown', unlockAudio)
      window.removeEventListener('keydown', unlockAudio)
    }
  }, [ensureAudioContext, refreshNotificationPermission])

  useEffect(() => () => {
    liveNotificationsRef.current.forEach((notification) => {
      notification.close()
    })
    liveNotificationsRef.current.clear()
  }, [])

  const requestNotificationPermission = useCallback(async () => {
    if (!nativeNotificationsSupported()) {
      setNotificationPermission('unsupported')
      return 'unsupported' as const
    }
    if (Notification.permission !== 'default') {
      setNotificationPermission(Notification.permission)
      return Notification.permission
    }
    const next = await Notification.requestPermission()
    setNotificationPermission(next)
    return next
  }, [])

  const playNotificationSound = useCallback(async () => {
    const context = await ensureAudioContext()
    if (!context || context.state !== 'running') {
      return
    }

    const now = context.currentTime
    const masterGain = context.createGain()
    masterGain.gain.setValueAtTime(0.0001, now)
    masterGain.gain.exponentialRampToValueAtTime(0.025, now + 0.02)
    masterGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35)
    masterGain.connect(context.destination)

    const primary = context.createOscillator()
    primary.type = 'sine'
    primary.frequency.setValueAtTime(880, now)
    primary.frequency.exponentialRampToValueAtTime(1320, now + 0.12)
    primary.connect(masterGain)
    primary.start(now)
    primary.stop(now + 0.18)

    const accentGain = context.createGain()
    accentGain.gain.setValueAtTime(0.0001, now + 0.04)
    accentGain.gain.exponentialRampToValueAtTime(0.012, now + 0.07)
    accentGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.24)
    accentGain.connect(context.destination)

    const accent = context.createOscillator()
    accent.type = 'triangle'
    accent.frequency.setValueAtTime(660, now + 0.04)
    accent.frequency.exponentialRampToValueAtTime(990, now + 0.16)
    accent.connect(accentGain)
    accent.start(now + 0.04)
    accent.stop(now + 0.24)
  }, [ensureAudioContext])

  const handleMessageNotificationEvent = useCallback((event: AgentMessageNotification) => {
    if (!enabled || !shouldDispatchAgentChatNotification({
      event,
      currentContext,
      activeAgentId,
      availableAgentIds,
    })) {
      return
    }

    if (seenMessageIdsRef.current.has(event.message.id)) {
      return
    }
    seenMessageIdsRef.current.add(event.message.id)
    if (seenMessageIdsRef.current.size > 250) {
      seenMessageIdsRef.current = new Set(Array.from(seenMessageIdsRef.current).slice(-125))
    }

    void playNotificationSound()

    if (readAgentChatNotificationPermission() !== 'granted') {
      refreshNotificationPermission()
      return
    }

    const notification = new Notification(buildNotificationTitle(event), {
      body: buildNotificationBody(event),
      icon: event.agent_avatar_url ?? undefined,
      tag: `agent-message:${event.message.id}`,
      silent: true,
    })
    liveNotificationsRef.current.set(event.message.id, notification)
    notification.onclick = () => {
      notification.close()
      window.focus()
      openAgentRef.current(event.agent_id)
    }
    notification.onclose = () => {
      liveNotificationsRef.current.delete(event.message.id)
    }
  }, [
    activeAgentId,
    availableAgentIds,
    currentContext,
    enabled,
    playNotificationSound,
    refreshNotificationPermission,
  ])

  const notificationStatus = useMemo(
    () => resolveAgentChatNotificationStatus(enabled, notificationPermission),
    [enabled, notificationPermission],
  )

  return {
    notificationPermission,
    notificationStatus,
    refreshNotificationPermission,
    requestNotificationPermission,
    handleMessageNotificationEvent,
  }
}
