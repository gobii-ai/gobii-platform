export type AgentChatSidebarMode = 'collapsed' | 'list' | 'gallery'

export type AgentDrawerViewMode = 'list' | 'gallery'

export const SIDEBAR_MOBILE_BREAKPOINT_PX = 768

export function getInitialAgentChatSidebarMode(): AgentChatSidebarMode {
  if (typeof window === 'undefined') {
    return 'list'
  }
  return window.innerWidth < SIDEBAR_MOBILE_BREAKPOINT_PX ? 'collapsed' : 'list'
}

export function getPreviousAgentChatSidebarMode(mode: AgentChatSidebarMode): AgentChatSidebarMode {
  switch (mode) {
    case 'gallery':
      return 'list'
    case 'list':
      return 'collapsed'
    case 'collapsed':
    default:
      return 'collapsed'
  }
}

export function getNextAgentChatSidebarMode(mode: AgentChatSidebarMode): AgentChatSidebarMode {
  switch (mode) {
    case 'collapsed':
      return 'list'
    case 'list':
      return 'gallery'
    case 'gallery':
    default:
      return 'gallery'
  }
}
