export type AgentChatShellSubview = 'chat' | 'settings' | 'secrets' | 'secret-requests' | 'email' | 'files' | 'contact-requests'

const CONSOLE_SHELL_SUBVIEW_PATTERN = '(settings|secrets|email|files|contact-requests)'

function normalizeSubviewToken(token?: string | null): AgentChatShellSubview {
  switch (token) {
    case 'settings':
    case 'secrets':
    case 'secret-requests':
    case 'email':
    case 'files':
    case 'contact-requests':
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
  const appParts = normalized.split('/').filter(Boolean)
  if (appParts[0] === 'app' && appParts[1] === 'agents' && appParts[2] === 'new') {
    return null
  }
  if (
    appParts[0] === 'app'
    && appParts[1] === 'agents'
    && appParts[2]
    && (
      appParts.length === 3
      || (appParts.length === 4 && ['settings', 'secrets', 'email', 'files', 'contact-requests'].includes(appParts[3]))
      || (appParts.length === 5 && appParts[3] === 'secrets' && appParts[4] === 'request')
    )
  ) {
    return appParts[2]
  }

  const appMatch = normalized.match(/^\/app\/agents\/([^/]+)$/)
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
  const appParts = normalized.split('/').filter(Boolean)
  if (appParts[0] === 'app' && appParts[1] === 'agents' && appParts[2]) {
    if (appParts[3] === 'secrets' && appParts[4] === 'request' && appParts.length === 5) {
      return 'secret-requests'
    }
    if (appParts.length === 3 || appParts.length === 4) {
      return normalizeSubviewToken(appParts[3])
    }
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
      case 'secret-requests':
        return `/app/agents/${agentId}/secrets/request`
      case 'email':
        return `/app/agents/${agentId}/email`
      case 'files':
        return `/app/agents/${agentId}/files`
      case 'contact-requests':
        return `/app/agents/${agentId}/contact-requests`
      default:
        return `/app/agents/${agentId}`
    }
  }
  switch (subview) {
    case 'settings':
      return `/console/agents/${agentId}/chat/settings/`
    case 'secrets':
      return `/console/agents/${agentId}/chat/secrets/`
    case 'secret-requests':
      return `/console/agents/${agentId}/secrets/request/`
    case 'email':
      return `/console/agents/${agentId}/chat/email/`
    case 'files':
      return `/console/agents/${agentId}/chat/files/`
    case 'contact-requests':
      return `/console/agents/${agentId}/chat/contact-requests/`
    default:
      return `/console/agents/${agentId}/chat/`
  }
}

export function buildAgentChatShellSelectionPath(pathname: string): string {
  return pathname.startsWith('/app') ? '/app/agents' : '/console/agents'
}
