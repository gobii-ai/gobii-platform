import { useEffect, useRef } from 'react'
import { Settings } from 'lucide-react'

import { normalizeHexColor } from '../../util/color'

type AgentChatBannerProps = {
  agentName: string
  agentAvatarUrl?: string | null
  agentDetailUrl?: string | null
  agentColorHex?: string | null
}

export function AgentChatBanner({ agentName, agentAvatarUrl, agentDetailUrl, agentColorHex }: AgentChatBannerProps) {
  const trimmedName = agentName.trim() || 'Agent'
  const nameParts = trimmedName.split(/\s+/).filter(Boolean)
  const firstInitial = nameParts[0]?.charAt(0).toUpperCase() || 'A'
  const lastInitial = nameParts.length > 1 ? nameParts[nameParts.length - 1]?.charAt(0).toUpperCase() || '' : ''
  const initials = `${firstInitial}${lastInitial}`.trim()
  const accentColor = normalizeHexColor(agentColorHex)
  const hasAvatar = Boolean(agentAvatarUrl)
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
            <div
              className="flex h-12 w-12 items-center justify-center overflow-hidden rounded-full border bg-white"
              style={{ borderColor: accentColor }}
            >
              {hasAvatar ? (
                <img src={agentAvatarUrl ?? undefined} alt={`${trimmedName} avatar`} className="h-full w-full object-cover" />
              ) : (
                <span
                  className="flex h-full w-full items-center justify-center text-sm font-semibold text-white"
                  style={{ background: `linear-gradient(135deg, ${accentColor}, #0f172a)` }}
                >
                  {initials || 'A'}
                </span>
              )}
            </div>
            <div>
              <div className="text-[0.7rem] font-semibold uppercase tracking-[0.24em] text-slate-500">Live chat</div>
              <div className="text-lg font-semibold text-slate-900">{trimmedName}</div>
            </div>
          </div>
          {agentDetailUrl ? (
            <a
              href={agentDetailUrl}
              className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white transition hover:bg-slate-800"
            >
              <Settings className="h-4 w-4" aria-hidden />
              Configure
            </a>
          ) : null}
        </div>
      </div>
    </div>
  )
}
