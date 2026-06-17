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
