import type { NativeIntegrationProvider } from '../../api/nativeIntegrations'

export const SLACK_NATIVE_PROVIDER_KEY = 'slack'

export const SLACK_NATIVE_DISPLAY_PROVIDER: NativeIntegrationProvider = {
  providerKey: SLACK_NATIVE_PROVIDER_KEY,
  displayName: 'Slack',
  description: 'Connect Slack workspaces and subscribe agents to selected channels.',
  authType: 'oauth2',
  icon: 'slack',
  apiHosts: ['slack.com'],
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
