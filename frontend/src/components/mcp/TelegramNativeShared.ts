import type { NativeIntegrationProvider } from '../../api/nativeIntegrations'

export const TELEGRAM_NATIVE_PROVIDER_KEY = 'telegram'

export const TELEGRAM_NATIVE_DISPLAY_PROVIDER: NativeIntegrationProvider = {
  providerKey: TELEGRAM_NATIVE_PROVIDER_KEY,
  displayName: 'Telegram',
  description: 'Create a managed Telegram bot identity for each agent.',
  authType: 'custom',
  icon: 'telegram',
  apiHosts: ['telegram.org'],
  scopes: [],
  connected: false,
  scope: 'personal',
  expiresAt: null,
  connectUrl: '',
  filesUrl: '',
  pickerTokenUrl: '',
  agentEventUrl: '',
  revokeUrl: '',
}

export function withTelegramNativeProvider(providers: NativeIntegrationProvider[]): NativeIntegrationProvider[] {
  if (providers.some((provider) => provider.providerKey === TELEGRAM_NATIVE_PROVIDER_KEY)) {
    return providers
  }
  return [...providers, TELEGRAM_NATIVE_DISPLAY_PROVIDER]
}

export function withTelegramNativeProviderConnection(
  providers: NativeIntegrationProvider[],
  connected: boolean,
): NativeIntegrationProvider[] {
  const nextProviders = withTelegramNativeProvider(providers)
  return nextProviders.map((provider) => (
    provider.providerKey === TELEGRAM_NATIVE_PROVIDER_KEY
      ? { ...provider, connected }
      : provider
  ))
}

function isTelegramWebHost(hostname: string): boolean {
  return ['t.me', 'telegram.me', 'telegram.dog'].includes(hostname.toLowerCase())
}

export function telegramAppUrlForWebUrl(webUrl: string): string {
  try {
    const parsed = new URL(webUrl)
    if (!isTelegramWebHost(parsed.hostname)) {
      return ''
    }
    const pathParts = parsed.pathname.split('/').filter(Boolean)
    const firstPathPart = pathParts[0] ?? ''
    if (!firstPathPart) {
      return ''
    }

    if (firstPathPart.toLowerCase() === 'newbot') {
      const encodedPath = pathParts.map((part) => encodeURIComponent(part)).join('/')
      return `tg://${encodedPath}${parsed.search}`
    }

    const params = new URLSearchParams({ domain: firstPathPart })
    parsed.searchParams.forEach((value, key) => {
      if (key !== 'domain') {
        params.append(key, value)
      }
    })
    const query = params.toString()
    return query ? `tg://resolve?${query}` : `tg://resolve?domain=${encodeURIComponent(firstPathPart)}`
  } catch {
    return ''
  }
}

export function openTelegramHandoff(webUrl: string): { appUrl: string; webUrl: string } {
  const appUrl = telegramAppUrlForWebUrl(webUrl)
  const urlToOpen = appUrl || webUrl
  if (urlToOpen) {
    window.open(urlToOpen, '_blank', 'noopener,noreferrer')
  }
  return { appUrl, webUrl }
}
