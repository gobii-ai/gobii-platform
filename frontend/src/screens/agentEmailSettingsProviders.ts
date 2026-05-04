import type { EmailProviderDefaults } from '../api/agentEmailSettings'

export type ProviderConfig = {
  label: string
  authorizationEndpoint: string
  tokenEndpoint: string
  scope: string
  authorizationParams: Record<string, string>
  guidanceTitle?: string
  guidanceBody?: string
  guidanceImage?: string
  guidanceConfirmLabel?: string
  guidanceContinueLabel?: string
}

function defineProviderConfig<const T extends Record<string, ProviderConfig>>(
  config: T,
): { [K in keyof T]: ProviderConfig } {
  return config
}

export const EMAIL_OAUTH_PROVIDER_CONFIG = defineProviderConfig({
  gmail: {
    label: 'Gmail',
    authorizationEndpoint: 'https://accounts.google.com/o/oauth2/v2/auth',
    tokenEndpoint: 'https://oauth2.googleapis.com/token',
    scope: 'https://mail.google.com/',
    authorizationParams: {
      access_type: 'offline',
      prompt: 'consent',
    },
    guidanceTitle: 'Before you continue to Google',
    guidanceBody: 'If Google shows an unverified-app warning, click Advanced, then Go to Gobii (unsafe).',
    guidanceImage: '/static/images/email/google-oauth-advanced-warning.png',
    guidanceConfirmLabel: 'I understand how to proceed.',
    guidanceContinueLabel: 'Continue to Google',
  },
  microsoft: {
    label: 'Microsoft 365',
    authorizationEndpoint: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    tokenEndpoint: 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
    scope: 'offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send',
    authorizationParams: {
      prompt: 'select_account',
    },
  },
  outlook: {
    label: 'Outlook.com',
    authorizationEndpoint: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    tokenEndpoint: 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
    scope: 'offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send',
    authorizationParams: {
      prompt: 'select_account',
    },
  },
})

export type OAuthProviderKey = keyof typeof EMAIL_OAUTH_PROVIDER_CONFIG
export type ProviderKey = OAuthProviderKey | 'custom'

const OAUTH_PROVIDER_KEYS = Object.keys(EMAIL_OAUTH_PROVIDER_CONFIG) as OAuthProviderKey[]

export const EMAIL_PROVIDER_OPTIONS: ReadonlyArray<{ value: ProviderKey; label: string }> = [
  ...OAUTH_PROVIDER_KEYS.map((provider) => ({
    value: provider,
    label: EMAIL_OAUTH_PROVIDER_CONFIG[provider].label,
  })),
  { value: 'custom', label: 'Other provider' },
]

export function isOAuthProviderKey(value: string): value is OAuthProviderKey {
  return Object.prototype.hasOwnProperty.call(EMAIL_OAUTH_PROVIDER_CONFIG, value)
}

export function getProviderLabel(provider: string): string {
  return isOAuthProviderKey(provider) ? EMAIL_OAUTH_PROVIDER_CONFIG[provider].label : 'OAuth'
}

export function matchProviderFromDefaults(
  providerDefaults: Record<string, EmailProviderDefaults | undefined>,
  smtpHost: string,
): OAuthProviderKey | null {
  const normalizedHost = smtpHost.trim().toLowerCase()
  if (!normalizedHost) {
    return null
  }
  for (const provider of OAUTH_PROVIDER_KEYS) {
    const defaults = providerDefaults[provider]
    if (defaults && defaults.smtp_host.toLowerCase() === normalizedHost) {
      return provider
    }
  }
  return null
}
