export type AgentChatShellSubview = 'chat' | 'settings' | 'secrets' | 'email' | 'files'

const APP_SHELL_SUBVIEW_PATTERN = '(settings|secrets|email|files)'
const CONSOLE_SHELL_SUBVIEW_PATTERN = '(settings|secrets|email|files)'

function normalizeSubviewToken(token?: string | null): AgentChatShellSubview {
  switch (token) {
    case 'settings':
    case 'secrets':
    case 'email':
    case 'files':
      return token
    default:
      return 'chat'
  }
}

function normalizePathname(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, '')
  return trimmed || '/'
}

export function extractAgentChatShellAgentId(pathname: string): string | null {
  const normalized = normalizePathname(pathname)
  const appMatch = normalized.match(new RegExp(`^/app/agents/([^/]+)(?:/${APP_SHELL_SUBVIEW_PATTERN})?$`))
  if (appMatch) {
    return appMatch[1]
  }

  const consoleMatch = normalized.match(new RegExp(`^/console/agents/([^/]+)/chat(?:/${CONSOLE_SHELL_SUBVIEW_PATTERN})?$`))
  if (consoleMatch) {
    return consoleMatch[1]
  }

  return null
}

export function getAgentChatShellSubview(pathname: string): AgentChatShellSubview {
  const normalized = normalizePathname(pathname)
  const appMatch = normalized.match(new RegExp(`^/app/agents/[^/]+(?:/${APP_SHELL_SUBVIEW_PATTERN})?$`))
  if (appMatch) {
    return normalizeSubviewToken(appMatch[1])
  }

  const consoleMatch = normalized.match(new RegExp(`^/console/agents/[^/]+/chat(?:/${CONSOLE_SHELL_SUBVIEW_PATTERN})?$`))
  if (consoleMatch) {
    return normalizeSubviewToken(consoleMatch[1])
  }

  return 'chat'
}

export function buildAgentChatShellPath(
  pathname: string,
  agentId: string,
  subview: AgentChatShellSubview = 'chat',
): string {
  if (pathname.startsWith('/app')) {
    switch (subview) {
      case 'settings':
        return `/app/agents/${agentId}/settings`
      case 'secrets':
        return `/app/agents/${agentId}/secrets`
      case 'email':
        return `/app/agents/${agentId}/email`
      case 'files':
        return `/app/agents/${agentId}/files`
      default:
        return `/app/agents/${agentId}`
    }
  }
  switch (subview) {
    case 'settings':
      return `/console/agents/${agentId}/chat/settings/`
    case 'secrets':
      return `/console/agents/${agentId}/chat/secrets/`
    case 'email':
      return `/console/agents/${agentId}/chat/email/`
    case 'files':
      return `/console/agents/${agentId}/chat/files/`
    default:
      return `/console/agents/${agentId}/chat/`
  }
}

export function buildAgentChatShellSelectionPath(pathname: string): string {
  return pathname.startsWith('/app') ? '/app/agents' : '/console/agents'
}
