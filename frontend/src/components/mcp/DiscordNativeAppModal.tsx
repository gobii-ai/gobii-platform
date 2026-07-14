import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, Hash, Loader2, Plug, Save, Settings } from 'lucide-react'

import {
  agentDiscordAppQueryKey,
  disconnectDiscordNative,
  fetchAgentDiscordGuildChannels,
  startAgentDiscordConnect,
  updateAgentDiscordSubscriptions,
  type AgentDiscordApp,
  type DiscordChannel,
  type DiscordGuild,
  type DiscordSubscription,
  type DiscordSubscriptionSelection,
} from '../../api/discordNative'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import type { AgentRosterEntry } from '../../types/agentRoster'
import { useSettingsSurfaceVariant } from '../common/SettingsSurface'
import { PipedreamEmptyState, PipedreamErrorState, PipedreamListFrame, PipedreamLoadingState, PipedreamStatusBanner, type PipedreamStatusMessage } from './PipedreamAppsShared'

export type PendingDiscordAction = 'connect' | 'save' | null

export type PendingDiscordAgentAction = {
  agentId: string
  kind: Exclude<PendingDiscordAction, null>
} | null

const DISCORD_NATIVE_SYSTEM_SKILL_KEY = 'discord_native'

export function agentHasDiscordNative(agent: AgentRosterEntry): boolean {
  return Boolean(agent.enabledSystemSkills?.includes(DISCORD_NATIVE_SYSTEM_SKILL_KEY))
}

export function useDiscordOAuthCompleteRefetch({
  agentId,
  onError,
}: {
  agentId?: string | null
  onError: (message: string) => void
}) {
  const queryClient = useQueryClient()

  useEffect(() => {
    const handleDiscordOAuthComplete = (event: MessageEvent<{ type?: unknown; status?: unknown; agent_id?: unknown }>) => {
      if (event.origin !== window.location.origin || event.data?.type !== 'gobii:discord_oauth_complete') {
        return
      }
      const completedAgentId = typeof event.data.agent_id === 'string' ? event.data.agent_id : agentId
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
      if (completedAgentId) {
        void queryClient.invalidateQueries({ queryKey: agentDiscordAppQueryKey(completedAgentId) })
      }
      if (event.data.status !== 'success') {
        onError('Unable to complete the Discord connection.')
      }
    }
    window.addEventListener('message', handleDiscordOAuthComplete)
    return () => window.removeEventListener('message', handleDiscordOAuthComplete)
  }, [agentId, onError, queryClient])
}

export function useDiscordNativeAgentActions({
  onStart,
  onError,
}: {
  onStart?: () => void
  onError: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [pendingDiscordAgentAction, setPendingDiscordAgentAction] = useState<PendingDiscordAgentAction>(null)

  const connectMutation = useMutation({
    mutationFn: (agentId: string) => startAgentDiscordConnect(agentId),
    onMutate: (agentId) => {
      onStart?.()
      setPendingDiscordAgentAction({ agentId, kind: 'connect' })
    },
    onSuccess: (result, agentId) => {
      queryClient.setQueryData(agentDiscordAppQueryKey(agentId), result.app)
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
      window.open(result.connectUrl, '_blank')
    },
    onError: (error) => onError(safeErrorMessage(error)),
    onSettled: () => setPendingDiscordAgentAction(null),
  })

  const subscriptionsMutation = useMutation({
    mutationFn: ({ agentId, subscriptions }: { agentId: string; subscriptions: DiscordSubscriptionSelection[] }) =>
      updateAgentDiscordSubscriptions(agentId, subscriptions),
    onMutate: ({ agentId }) => {
      onStart?.()
      setPendingDiscordAgentAction({ agentId, kind: 'save' })
    },
    onSuccess: (app, { agentId }) => {
      queryClient.setQueryData(agentDiscordAppQueryKey(agentId), app)
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
    },
    onError: (error) => onError(safeErrorMessage(error)),
    onSettled: () => setPendingDiscordAgentAction(null),
  })

  return {
    connectDiscordAgent: (agentId: string) => connectMutation.mutate(agentId),
    saveDiscordAgentSubscriptions: (agentId: string, subscriptions: DiscordSubscriptionSelection[]) =>
      subscriptionsMutation.mutate({ agentId, subscriptions }),
    pendingDiscordAgentAction,
    isDiscordAgentActionPending: connectMutation.isPending || subscriptionsMutation.isPending,
  }
}

export function useDiscordNativeDisconnect({
  onMutate,
  onSuccess,
  onError,
  onSettled,
}: {
  onMutate: () => void
  onSuccess: () => void
  onError: (message: string) => void
  onSettled?: () => void
}) {
  return useMutation({
    mutationFn: disconnectDiscordNative,
    onMutate,
    onSuccess,
    onError: (error) => onError(safeErrorMessage(error)),
    onSettled,
  })
}

export function DiscordConfigurationScreen({
  agentId,
  app,
  disabled,
  pendingDiscordAction,
  statusMessage,
  onBack,
  onSave,
}: {
  agentId: string
  app: AgentDiscordApp
  disabled: boolean
  pendingDiscordAction: PendingDiscordAction
  statusMessage: PipedreamStatusMessage
  onBack: () => void
  onSave: (subscriptions: DiscordSubscriptionSelection[]) => void
}) {
  const surface = useSettingsSurfaceVariant()
  const initialSelections = useMemo(() => activeDiscordSelections(app), [app.subscriptions])
  const [selectedSubscriptions, setSelectedSubscriptions] = useState<Record<string, DiscordSubscriptionSelection>>(initialSelections)
  const savedKeys = useMemo(() => Object.keys(initialSelections).sort().join('|'), [initialSelections])
  const [lastSavedKeys, setLastSavedKeys] = useState(savedKeys)

  if (savedKeys !== lastSavedKeys) {
    setLastSavedKeys(savedKeys)
    setSelectedSubscriptions(initialSelections)
  }

  const selectedKeys = useMemo(() => Object.keys(selectedSubscriptions).sort(), [selectedSubscriptions])
  const hasChanges = selectedKeys.join('|') !== savedKeys
  const isPendingSave = pendingDiscordAction === 'save'
  const saveButtonClassName = surface === 'embedded'
    ? 'border border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75'
    : 'bg-blue-600 text-white hover:bg-blue-700'

  const toggleChannel = (channel: DiscordChannel) => {
    const key = discordSubscriptionKey(channel.guildId, channel.channelId)
    setSelectedSubscriptions((current) => {
      if (current[key]) {
        const next = { ...current }
        delete next[key]
        return next
      }
      return {
        ...current,
        [key]: {
          guildId: channel.guildId,
          channelId: channel.channelId,
          channelName: channel.channelName,
        },
      }
    })
  }

  return (
    <div className="space-y-4 p-1">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <BackButton disabled={disabled} onClick={onBack} />
        <button
          type="button"
          className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${saveButtonClassName}`}
          onClick={() => onSave(Object.values(selectedSubscriptions))}
          disabled={disabled || isPendingSave || !hasChanges}
        >
          {isPendingSave ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Save className="h-4 w-4" aria-hidden="true" />}
          Save
        </button>
      </div>

      <DiscordSummaryCell app={app} />
      <PipedreamStatusBanner statusMessage={statusMessage} />

      {app.guilds.length > 0 ? (
        <div className="space-y-3">
          {app.guilds.map((guild) => (
            <DiscordGuildChannelSection
              key={guild.guildId}
              agentId={agentId}
              guild={guild}
              subscriptions={app.subscriptions}
              selectedSubscriptions={selectedSubscriptions}
              disabled={disabled}
              onToggleChannel={toggleChannel}
            />
          ))}
        </div>
      ) : (
        <PipedreamEmptyState label="No Discord servers are connected yet." />
      )}
    </div>
  )
}

export function DiscordAgentConnectionsScreen({
  agents,
  isLoading,
  isFetching,
  isError,
  error,
  isBusy,
  pendingDiscordAgentAction,
  statusMessage,
  onBack,
  onConnect,
  onConfigure,
}: {
  agents: AgentRosterEntry[]
  isLoading: boolean
  isFetching: boolean
  isError: boolean
  error: unknown
  isBusy: boolean
  pendingDiscordAgentAction: PendingDiscordAgentAction
  statusMessage: PipedreamStatusMessage
  onBack: () => void
  onConnect: (agent: AgentRosterEntry) => void
  onConfigure: (agent: AgentRosterEntry) => void
}) {
  const surface = useSettingsSurfaceVariant()
  const sortedAgents = useMemo(() => (
    [...agents].sort((a, b) => Number(!agentHasDiscordNative(a)) - Number(!agentHasDiscordNative(b)) || a.name.localeCompare(b.name))
  ), [agents])

  return (
    <div className="space-y-4 p-1">
      <BackButton disabled={isBusy} onClick={onBack} />

      <div className="flex items-center gap-3">
        <DiscordIconTile />
        <div className="min-w-0">
          <p className={`truncate text-sm font-semibold ${surface === 'embedded' ? 'text-slate-100' : 'text-slate-900'}`}>Discord</p>
          <p className={`text-sm ${surface === 'embedded' ? 'text-slate-400' : 'text-slate-600'}`}>{isFetching ? 'Refreshing connections…' : 'Configure Discord channels per agent.'}</p>
        </div>
      </div>

      <PipedreamStatusBanner statusMessage={statusMessage} />

      {isError ? (
        <PipedreamErrorState error={error} fallback="Unable to load agents." />
      ) : isLoading ? (
        <PipedreamLoadingState label="Loading agents…" />
      ) : sortedAgents.length === 0 ? (
        <PipedreamEmptyState label="No agents found." />
      ) : (
        <PipedreamListFrame isMobile={false} constrainHeight={false}>
          {sortedAgents.map((agent) => (
            <DiscordAgentConnectionRow
              key={agent.id}
              agent={agent}
              pendingDiscordAgentAction={pendingDiscordAgentAction}
              disabled={isBusy}
              onConnect={() => onConnect(agent)}
              onConfigure={() => onConfigure(agent)}
            />
          ))}
        </PipedreamListFrame>
      )}
    </div>
  )
}

export function DiscordSummaryCell({
  app,
}: {
  app: AgentDiscordApp
}) {
  const surface = useSettingsSurfaceVariant()
  const detail = app.connected
    ? `${app.guildCount} ${app.guildCount === 1 ? 'server' : 'servers'} connected; ${app.activeSubscriptionCount} ${app.activeSubscriptionCount === 1 ? 'channel' : 'channels'} subscribed.`
    : app.description
  const titleClassName = surface === 'embedded' ? 'text-slate-100' : 'text-slate-900'
  const descriptionClassName = surface === 'embedded' ? 'text-slate-400' : 'text-slate-600'
  const badgeClassName = surface === 'embedded'
    ? 'border-emerald-300/25 bg-emerald-950/45 text-emerald-200'
    : 'border-emerald-200 bg-emerald-50 text-emerald-700'
  return (
    <div className="flex min-w-0 items-center gap-3">
      <DiscordIconTile />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className={`truncate text-sm font-semibold ${titleClassName}`}>{app.displayName}</p>
          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${badgeClassName}`}>
            Native
          </span>
        </div>
        <p className={`mt-1 line-clamp-2 text-sm ${descriptionClassName}`}>{detail}</p>
      </div>
    </div>
  )
}

export function BackButton({
  disabled,
  onClick,
}: {
  disabled: boolean
  onClick: () => void
}) {
  const surface = useSettingsSurfaceVariant()
  const className = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/20 text-slate-300 hover:border-slate-100/35 hover:bg-slate-900/40'
    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
  return (
    <button
      type="button"
      className={`inline-flex w-fit items-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${className}`}
      onClick={onClick}
      disabled={disabled}
    >
      <ArrowLeft className="h-4 w-4" aria-hidden="true" />
      Back
    </button>
  )
}

function DiscordAgentConnectionRow({
  agent,
  pendingDiscordAgentAction,
  disabled,
  onConnect,
  onConfigure,
}: {
  agent: AgentRosterEntry
  pendingDiscordAgentAction: PendingDiscordAgentAction
  disabled: boolean
  onConnect: () => void
  onConfigure: () => void
}) {
  const surface = useSettingsSurfaceVariant()
  const enabled = agentHasDiscordNative(agent)
  const pendingKind = pendingDiscordAgentAction?.agentId === agent.id ? pendingDiscordAgentAction.kind : null
  const fallbackAvatarClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-900 text-slate-200'
    : 'border-slate-200 bg-white text-slate-700'
  const titleClassName = surface === 'embedded' ? 'text-slate-100' : 'text-slate-900'
  const descriptionClassName = surface === 'embedded' ? 'text-slate-400' : 'text-slate-600'
  const enabledBadgeClassName = surface === 'embedded'
    ? 'border-sky-300/25 bg-sky-950/45 text-sky-200'
    : 'border-blue-200 bg-blue-50 text-blue-700'
  const disabledBadgeClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/20 text-slate-400'
    : 'border-slate-200 text-slate-500'
  const configureClassName = surface === 'embedded'
    ? 'border-sky-300/25 bg-sky-950/20 text-sky-100 hover:border-sky-200/40 hover:bg-sky-900/40'
    : 'border-blue-200 bg-white text-blue-700 hover:bg-blue-50'
  const connectClassName = surface === 'embedded'
    ? 'border border-sky-300/25 bg-sky-900/55 text-sky-50 hover:border-sky-200/40 hover:bg-sky-900/75'
    : 'bg-blue-600 text-white hover:bg-blue-700'

  return (
    <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_8rem_9rem] md:items-center">
      <div className="flex min-w-0 items-center gap-3">
        {agent.avatarUrl ? (
          <img
            src={agent.avatarUrl}
            alt=""
            className="h-9 w-9 rounded-full border border-slate-200 bg-white object-cover"
            loading="lazy"
          />
        ) : (
          <span className={`inline-flex h-9 w-9 items-center justify-center rounded-full border text-xs font-semibold uppercase ${fallbackAvatarClassName}`}>
            {agent.name.slice(0, 2)}
          </span>
        )}
        <div className="min-w-0">
          <p className={`truncate text-sm font-semibold ${titleClassName}`}>{agent.name}</p>
          <p className={`truncate text-sm ${descriptionClassName}`}>
            {enabled ? 'Discord is enabled for this agent.' : 'Connect Discord before selecting channels.'}
          </p>
        </div>
      </div>
      <div>
        {enabled ? (
          <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${enabledBadgeClassName}`}>
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            Enabled
          </span>
        ) : (
          <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${disabledBadgeClassName}`}>
            Not enabled
          </span>
        )}
      </div>
      <div className="flex justify-start md:justify-end">
        {enabled ? (
          <button
            type="button"
            className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${configureClassName}`}
            onClick={onConfigure}
            disabled={disabled}
          >
            <Settings className="h-4 w-4" aria-hidden="true" />
            Configure
          </button>
        ) : (
          <button
            type="button"
            className={`inline-flex min-w-28 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition disabled:opacity-60 ${connectClassName}`}
            onClick={onConnect}
            disabled={disabled}
          >
            {pendingKind === 'connect' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Plug className="h-4 w-4" aria-hidden="true" />}
            Connect
          </button>
        )}
      </div>
    </div>
  )
}

function DiscordGuildChannelSection({
  agentId,
  guild,
  subscriptions,
  selectedSubscriptions,
  disabled,
  onToggleChannel,
}: {
  agentId: string
  guild: DiscordGuild
  subscriptions: DiscordSubscription[]
  selectedSubscriptions: Record<string, DiscordSubscriptionSelection>
  disabled: boolean
  onToggleChannel: (channel: DiscordChannel) => void
}) {
  const surface = useSettingsSurfaceVariant()
  const channelsQuery = useQuery({
    queryKey: ['agent-discord-channels', agentId, guild.guildId],
    queryFn: () => fetchAgentDiscordGuildChannels(agentId, guild.guildId),
  })
  const channelsByKey = new Map<string, DiscordChannel>()
  for (const channel of [
    ...subscriptions
      .filter((subscription) => subscription.status === 'active' && subscription.guildId === guild.guildId)
      .map((subscription): DiscordChannel => ({
        guildId: subscription.guildId,
        guildName: subscription.guildName,
        channelId: subscription.channelId,
        channelName: subscription.channelName,
        label: `${subscription.guildName} / #${subscription.channelName || subscription.channelId}`,
      })),
    ...(channelsQuery.data?.channels ?? []),
  ]) {
    channelsByKey.set(discordSubscriptionKey(channel.guildId, channel.channelId), channel)
  }
  const channels = Array.from(channelsByKey.values()).sort((a, b) => a.channelName.localeCompare(b.channelName))
  const sectionClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/30'
    : 'border-indigo-100 bg-white'
  const titleClassName = surface === 'embedded' ? 'text-slate-100' : 'text-slate-900'
  const descriptionClassName = 'text-slate-500'
  const actionRequiredClassName = surface === 'embedded'
    ? 'border-amber-300/25 bg-amber-950/35 text-amber-200'
    : 'border-amber-200 bg-amber-50 text-amber-800'
  const actionLinkClassName = surface === 'embedded' ? 'text-amber-100' : 'text-amber-900'
  const errorClassName = surface === 'embedded'
    ? 'border-rose-300/25 bg-rose-950/35 text-rose-200'
    : 'border-red-200 bg-red-50 text-red-700'
  const channelClassName = surface === 'embedded'
    ? 'border-slate-200/20 bg-slate-950/25 text-slate-300 hover:border-sky-300/35 hover:text-slate-100'
    : 'border-slate-200 bg-white text-slate-700 hover:border-indigo-200 hover:text-slate-950'
  const emptyClassName = surface === 'embedded' ? 'text-slate-400' : 'text-slate-600'

  return (
    <section className={`rounded-lg border px-3 py-3 ${sectionClassName}`} aria-label={`${guild.name} Discord channels`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className={`truncate text-sm font-semibold ${titleClassName}`}>{guild.name}</p>
          <p className={`text-xs ${descriptionClassName}`}>Choose channels that should wake this agent.</p>
        </div>
        {channelsQuery.isLoading ? <Loader2 className="h-4 w-4 animate-spin text-indigo-600" aria-hidden="true" /> : null}
      </div>
      {channelsQuery.data?.status === 'action_required' ? (
        <div className={`mt-3 rounded-md border px-3 py-2 text-sm ${actionRequiredClassName}`}>
          <p>{channelsQuery.data.message || 'The Gobii Discord bot needs access to list channels in this server.'}</p>
          {channelsQuery.data.botInviteUrl ? (
            <a href={channelsQuery.data.botInviteUrl} target="_blank" rel="noreferrer" className={`mt-2 inline-flex font-semibold underline ${actionLinkClassName}`}>
              Invite bot
            </a>
          ) : null}
        </div>
      ) : channelsQuery.isError ? (
        <div className={`mt-3 rounded-md border px-3 py-2 text-sm ${errorClassName}`}>
          {safeErrorMessage(channelsQuery.error)}
        </div>
      ) : channels.length > 0 ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {channels.map((channel) => {
            const key = discordSubscriptionKey(channel.guildId, channel.channelId)
            return (
              <label
                key={key}
                className={`flex min-w-0 items-center gap-2 rounded-md border px-3 py-2 text-sm transition ${channelClassName}`}
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                  checked={Boolean(selectedSubscriptions[key])}
                  onChange={() => onToggleChannel(channel)}
                  disabled={disabled}
                />
                <Hash className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
                <span className="truncate">{channel.channelName || channel.channelId}</span>
              </label>
            )
          })}
        </div>
      ) : channelsQuery.isLoading ? null : (
        <p className={`mt-3 text-sm ${emptyClassName}`}>No text channels were found for this server.</p>
      )}
    </section>
  )
}

function DiscordIconTile() {
  return (
    <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-700">
      <img src="/static/images/integrations/native/discord.svg" alt="" className="h-6 w-6 object-contain" loading="lazy" />
    </span>
  )
}

function discordSubscriptionKey(guildId: string, channelId: string): string {
  return `${guildId}:${channelId}`
}

function activeDiscordSelections(app: AgentDiscordApp): Record<string, DiscordSubscriptionSelection> {
  return Object.fromEntries(
    app.subscriptions
      .filter((subscription) => subscription.status === 'active')
      .map((subscription) => [
        discordSubscriptionKey(subscription.guildId, subscription.channelId),
        {
          guildId: subscription.guildId,
          channelId: subscription.channelId,
          channelName: subscription.channelName,
        },
      ]),
  )
}
