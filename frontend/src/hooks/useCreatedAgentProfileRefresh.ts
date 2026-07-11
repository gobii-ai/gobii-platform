import { useEffect } from 'react'

import { fetchAgentProfile } from '../api/agents'
import type { AgentRosterEntry } from '../types/agentRoster'

const PROFILE_REFRESH_DELAYS_MS = [5_000, 15_000, 30_000, 60_000, 90_000]

type UseCreatedAgentProfileRefreshOptions = {
  agentId: string | null
  avatarUrl: string | null | undefined
  onProfile: (profile: AgentRosterEntry) => void
}

export function useCreatedAgentProfileRefresh({
  agentId,
  avatarUrl,
  onProfile,
}: UseCreatedAgentProfileRefreshOptions): void {
  useEffect(() => {
    if (!agentId || avatarUrl) {
      return undefined
    }
    let cancelled = false
    let timer: number | undefined
    const startedAt = Date.now()
    const scheduleAttempt = (attempt: number): void => {
      const delay = PROFILE_REFRESH_DELAYS_MS[attempt]
      if (delay === undefined) return
      timer = window.setTimeout(async () => {
        const profile = await fetchAgentProfile(agentId).catch(() => null)
        if (cancelled) return
        if (profile) onProfile(profile)
        if (!profile?.avatarUrl) scheduleAttempt(attempt + 1)
      }, Math.max(0, startedAt + delay - Date.now()))
    }
    scheduleAttempt(0)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [agentId, avatarUrl, onProfile])
}
