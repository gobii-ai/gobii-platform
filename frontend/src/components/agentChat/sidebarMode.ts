export type AgentChatSidebarMode = 'collapsed' | 'list' | 'gallery'

export type AgentDrawerViewMode = 'list' | 'gallery'

export const SIDEBAR_MOBILE_BREAKPOINT_PX = 768

export function getInitialAgentChatSidebarMode(): AgentChatSidebarMode {
  if (typeof window === 'undefined') {
    return 'list'
  }
  return window.innerWidth < SIDEBAR_MOBILE_BREAKPOINT_PX ? 'collapsed' : 'list'
}
