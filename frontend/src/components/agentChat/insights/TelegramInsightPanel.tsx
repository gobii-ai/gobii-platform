import { useQuery } from '@tanstack/react-query'
import { Settings } from 'lucide-react'

import {
  agentTelegramAppQueryKey,
  fetchAgentTelegramApp,
} from '../../../api/telegramNative'
import type { NativeIntegrationProvider } from '../../../api/nativeIntegrations'
import { safeErrorMessage } from '../../../api/safeErrorMessage'
import { NativeIntegrationInsightPanelFrame } from './NativeIntegrationInsightPanel'

type TelegramInsightPanelProps = {
  agentId?: string | null
  nativeIntegrationsUrl?: string | null
  onOpenApps?: () => void
}

const TELEGRAM_FALLBACK_ICON = (
  <img src="/static/images/integrations/native/telegram.svg" alt="" className="h-5 w-5 object-contain" />
)

const TELEGRAM_PROVIDER: NativeIntegrationProvider = {
  providerKey: 'telegram',
  displayName: 'Telegram',
  description: 'Create and manage a dedicated Telegram bot for this agent.',
  authType: 'managed_bot',
  icon: 'telegram',
  apiHosts: [],
  scopes: [],
  connected: false,
  scope: '',
  expiresAt: null,
  connectUrl: '',
  filesUrl: '',
  pickerTokenUrl: '',
  agentEventUrl: '',
  revokeUrl: '',
}

export function TelegramInsightPanel({ agentId = null, onOpenApps }: TelegramInsightPanelProps) {
  const appQuery = useQuery({
    queryKey: agentId ? agentTelegramAppQueryKey(agentId) : ['agent-telegram-app', null],
    queryFn: () => fetchAgentTelegramApp(agentId as string),
    enabled: Boolean(agentId),
  })
  const app = appQuery.data ?? null
  const provider = app ? { ...TELEGRAM_PROVIDER, connected: app.connected } : TELEGRAM_PROVIDER
  const activeChatCount = app?.activeChatCount ?? 0

  return (
    <NativeIntegrationInsightPanelFrame
      ariaLabel="Telegram"
      providerLabel="Telegram"
      provider={provider}
      connected={Boolean(app?.connected)}
      fallbackIcon={TELEGRAM_FALLBACK_ICON}
      unavailableMessage={!agentId ? 'Telegram setup is unavailable for this agent.' : null}
      loadingMessage={appQuery.isLoading ? 'Loading Telegram...' : null}
      errorMessage={appQuery.isError ? safeErrorMessage(appQuery.error) : null}
      notConfiguredMessage="Telegram is not configured."
      title={app?.connected
        ? activeChatCount > 0
          ? 'Telegram chats active'
          : 'Telegram connected'
        : 'Connect Telegram'}
      text={app?.connected
        ? activeChatCount > 0
          ? `${activeChatCount} ${activeChatCount === 1 ? 'chat is' : 'chats are'} known for this agent.`
          : 'This agent has a dedicated Telegram bot for DMs and delivered group messages.'
        : 'Create a dedicated Telegram bot so this agent can receive and reply to Telegram chats.'}
      actions={onOpenApps ? (
        <button
          type="button"
          className="google-drive-insight-panel__button"
          onClick={onOpenApps}
        >
          <Settings className="h-4 w-4" aria-hidden="true" />
          Configure
        </button>
      ) : null}
    />
  )
}
