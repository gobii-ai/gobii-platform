import type { NativeIntegrationProvider } from '../../api/nativeIntegrations'

export const DISCORD_NATIVE_PROVIDER_KEY = 'discord'

export const DISCORD_NATIVE_DISPLAY_PROVIDER: NativeIntegrationProvider = {
  providerKey: DISCORD_NATIVE_PROVIDER_KEY,
  displayName: 'Discord',
  description: 'Connect Discord servers and subscribe agents to selected channels.',
  authType: 'oauth2',
  icon: 'discord',
  apiHosts: ['discord.com'],
  scopes: [],
  connected: false,
  scope: 'personal',
  expiresAt: null,
  connectUrl: '',
  filesUrl: '',
  pickerTokenUrl: '',
  revokeUrl: '',
}

export function withDiscordNativeProvider(providers: NativeIntegrationProvider[]): NativeIntegrationProvider[] {
  if (providers.some((provider) => provider.providerKey === DISCORD_NATIVE_PROVIDER_KEY)) {
    return providers
  }
  return [...providers, DISCORD_NATIVE_DISPLAY_PROVIDER]
}

export function withDiscordNativeProviderConnection(
  providers: NativeIntegrationProvider[],
  connected: boolean,
): NativeIntegrationProvider[] {
  const nextProviders = withDiscordNativeProvider(providers)
  return nextProviders.map((provider) => (
    provider.providerKey === DISCORD_NATIVE_PROVIDER_KEY
      ? { ...provider, connected }
      : provider
  ))
}
