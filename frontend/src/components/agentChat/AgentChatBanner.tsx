import { useEffect, useRef } from 'react'

import { AgentAvatarBadge } from '../common/AgentAvatarBadge'
import { normalizeHexColor } from '../../util/color'

type AgentChatBannerProps = {
  agentName: string
  agentAvatarUrl?: string | null
  agentColorHex?: string | null
}

export function AgentChatBanner({ agentName, agentAvatarUrl, agentColorHex }: AgentChatBannerProps) {
  const trimmedName = agentName.trim() || 'Agent'
  const accentColor = normalizeHexColor(agentColorHex)
  const bannerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const node = bannerRef.current
    if (!node || typeof window === 'undefined') return

    const updateHeight = () => {
      const height = node.getBoundingClientRect().height
      document.documentElement.style.setProperty('--agent-chat-banner-height', `${height}px`)
    }

    updateHeight()
    const observer = new ResizeObserver(updateHeight)
    observer.observe(node)

    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty('--agent-chat-banner-height')
    }
  }, [])

  return (
    <div className="fixed inset-x-0 top-0 z-30">
      <div className="mx-auto w-full max-w-5xl px-4 pb-3 pt-4 sm:px-6 lg:px-10" ref={bannerRef}>
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white/90 px-5 py-4 shadow-[0_12px_30px_rgba(15,23,42,0.12)] backdrop-blur">
          <div className="flex items-center gap-4">
            <AgentAvatarBadge
              name={trimmedName}
              avatarUrl={agentAvatarUrl}
              className="flex h-12 w-12 items-center justify-center overflow-hidden rounded-full border bg-white"
              imageClassName="h-full w-full object-cover"
              textClassName="flex h-full w-full items-center justify-center text-xl font-semibold text-white"
              style={{ borderColor: accentColor }}
              fallbackStyle={{ background: `linear-gradient(135deg, ${accentColor}, #0f172a)` }}
            />
            <div>
              <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-500">Live chat</div>
              <div className="text-lg font-semibold text-slate-900">{trimmedName}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
