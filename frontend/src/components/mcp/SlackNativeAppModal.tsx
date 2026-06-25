import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, Hash, Loader2, Plug, Save, Settings } from 'lucide-react'

import {
  agentSlackAppQueryKey,
  fetchAgentSlackChannels,
  startAgentSlackConnect,
  updateAgentSlackSubscriptions,
  type AgentSlackApp,
  type SlackChannel,
  type SlackSubscriptionSelection,
} from '../../api/slackNative'
import { startNativeIntegrationConnect } from '../../api/nativeIntegrations'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import {
  PipedreamErrorState,
  PipedreamLoadingState,
  PipedreamStatusBanner,
  type PipedreamStatusMessage,
} from './PipedreamAppsShared'
import {
  nativeOAuthContextPayload,
  openNativeOAuthPopup,
  storePendingNativeOAuth,
} from './NativeIntegrationShared'
import { SLACK_NATIVE_DISPLAY_PROVIDER } from './SlackNativeShared'

export type PendingSlackAction = 'connect' | 'save' | null

export type PendingSlackAgentAction = {
  agentId: string
  kind: Exclude<PendingSlackAction, null>
} | null

export function useSlackNativeAgentActions({
  onStart,
  onError,
}: {
  onStart?: () => void
  onError: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [pendingSlackAgentAction, setPendingSlackAgentAction] = useState<PendingSlackAgentAction>(null)

  const connectMutation = useMutation({
    mutationFn: async (agentId: string) => {
      const agentConnect = await startAgentSlackConnect(agentId)
      const provider = {
        ...SLACK_NATIVE_DISPLAY_PROVIDER,
        connectUrl: agentConnect.connectUrl,
      }
      const popup = openNativeOAuthPopup(provider)
      const oauth = await startNativeIntegrationConnect(agentConnect.connectUrl)
      return { agentId, app: agentConnect.app, oauth, popup, provider }
    },
    onMutate: (agentId) => {
      onStart?.()
      setPendingSlackAgentAction({ agentId, kind: 'connect' })
    },
    onSuccess: ({ agentId, app, oauth, popup, provider }) => {
      queryClient.setQueryData(agentSlackAppQueryKey(agentId), app)
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
      storePendingNativeOAuth(oauth.state, nativeOAuthContextPayload(provider, oauth.state, popup))
      if (popup && !popup.closed) {
        popup.location.href = oauth.authorizationUrl
        popup.focus()
        return
      }
      window.location.href = oauth.authorizationUrl
    },
    onError: (error) => onError(safeErrorMessage(error)),
    onSettled: () => setPendingSlackAgentAction(null),
  })

  const subscriptionsMutation = useMutation({
    mutationFn: ({ agentId, subscriptions }: { agentId: string; subscriptions: SlackSubscriptionSelection[] }) =>
      updateAgentSlackSubscriptions(agentId, subscriptions),
    onMutate: ({ agentId }) => {
      onStart?.()
      setPendingSlackAgentAction({ agentId, kind: 'save' })
    },
    onSuccess: (app, { agentId }) => {
      queryClient.setQueryData(agentSlackAppQueryKey(agentId), app)
      void queryClient.invalidateQueries({ queryKey: ['agent-roster'], exact: false })
    },
    onError: (error) => onError(safeErrorMessage(error)),
    onSettled: () => setPendingSlackAgentAction(null),
  })

  return {
    connectSlackAgent: (agentId: string) => connectMutation.mutate(agentId),
    saveSlackAgentSubscriptions: (agentId: string, subscriptions: SlackSubscriptionSelection[]) =>
      subscriptionsMutation.mutate({ agentId, subscriptions }),
    pendingSlackAgentAction,
    isSlackAgentActionPending: connectMutation.isPending || subscriptionsMutation.isPending,
  }
}

export function SlackConfigurationScreen({
  agentId,
  app,
  disabled,
  pendingSlackAction,
  statusMessage,
  onBack,
  onConnect,
  onSave,
}: {
  agentId: string
  app: AgentSlackApp
  disabled: boolean
  pendingSlackAction: PendingSlackAction
  statusMessage: PipedreamStatusMessage
  onBack: () => void
  onConnect: () => void
  onSave: (subscriptions: SlackSubscriptionSelection[]) => void
}) {
  const channelsQuery = useQuery({
    queryKey: ['agent-slack-channels', agentId],
    queryFn: () => fetchAgentSlackChannels(agentId),
    enabled: app.connected,
  })
  const [selectedSubscriptions, setSelectedSubscriptions] = useState<Record<string, SlackSubscriptionSelection>>(
    () => activeSlackSelections(app),
  )
  const channelsByKey = new Map<string, SlackChannel>()
  for (const channel of [
    ...app.subscriptions
      .filter((subscription) => subscription.status === 'active')
      .map((subscription): SlackChannel => ({
        workspaceId: subscription.workspaceId,
        teamId: subscription.teamId,
        teamName: subscription.teamName,
        channelId: subscription.channelId,
        channelName: subscription.channelName,
        channelType: subscription.channelType,
        label: `${subscription.teamName} / #${subscription.channelName || subscription.channelId}`,
      })),
    ...(channelsQuery.data?.channels ?? []),
  ]) {
    channelsByKey.set(slackSubscriptionKey(channel.workspaceId, channel.channelId), channel)
  }
  const channels = Array.from(channelsByKey.values()).sort((a, b) => a.label.localeCompare(b.label))
  const selectedCount = Object.keys(selectedSubscriptions).length
  const hasChanges = useMemo(() => {
    const original = activeSlackSelections(app)
    return JSON.stringify(original) !== JSON.stringify(selectedSubscriptions)
  }, [app, selectedSubscriptions])

  const toggleChannel = (channel: SlackChannel) => {
    const key = slackSubscriptionKey(channel.workspaceId, channel.channelId)
    setSelectedSubscriptions((current) => {
      if (current[key]) {
        const next = { ...current }
        delete next[key]
        return next
      }
      return {
        ...current,
        [key]: {
          workspaceId: channel.workspaceId,
          channelId: channel.channelId,
          channelName: channel.channelName,
          channelType: channel.channelType,
        },
      }
    })
  }

  return (
    <div className="space-y-4 p-1">
      <button
        type="button"
        className="inline-flex items-center gap-2 text-sm font-semibold text-slate-600 transition hover:text-slate-950"
        onClick={onBack}
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Apps
      </button>
      <PipedreamStatusBanner statusMessage={statusMessage} />
      <div className="rounded-lg border border-fuchsia-100 bg-white px-4 py-4">
        <div className="flex items-start gap-3">
          <SlackIconTile />
          <div className="min-w-0 flex-1">
            <h3 className="text-base font-semibold text-slate-950">Slack</h3>
            <p className="mt-1 text-sm text-slate-600">
              Subscribe this agent to public or private channels visible to the connected Slack app.
            </p>
            <p className="mt-2 text-xs text-slate-500">
              Replies can show this agent's name per message. Slack does not create separate mentionable bot users per agent.
            </p>
          </div>
          {app.connected ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              Connected
            </span>
          ) : null}
        </div>
        {!app.connected ? (
          <div className="mt-4">
            <button
              type="button"
              className="inline-flex items-center justify-center gap-2 rounded-md border border-fuchsia-200 bg-fuchsia-50 px-3 py-2 text-sm font-semibold text-fuchsia-800 transition hover:bg-fuchsia-100 disabled:opacity-60"
              onClick={onConnect}
              disabled={disabled}
            >
              {pendingSlackAction === 'connect' ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Plug className="h-4 w-4" aria-hidden="true" />
              )}
              Connect Slack
            </button>
          </div>
        ) : null}
      </div>
      {app.connected ? (
        <section className="rounded-lg border border-fuchsia-100 bg-white px-3 py-3" aria-label="Slack channels">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-slate-900">Channels</p>
              <p className="text-xs text-slate-500">Choose channels that should wake this agent.</p>
            </div>
            {channelsQuery.isLoading ? <Loader2 className="h-4 w-4 animate-spin text-fuchsia-600" aria-hidden="true" /> : null}
          </div>
          {channelsQuery.data?.status === 'action_required' ? (
            <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <p>{channelsQuery.data.message || 'Slack needs access to list channels.'}</p>
              {channelsQuery.data.setupUrl ? (
                <a href={channelsQuery.data.setupUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex font-semibold text-amber-900 underline">
                  Reconnect Slack
                </a>
              ) : null}
            </div>
          ) : channelsQuery.isError ? (
            <PipedreamErrorState error={channelsQuery.error} fallback="Unable to load Slack channels." />
          ) : channels.length > 0 ? (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {channels.map((channel) => {
                const key = slackSubscriptionKey(channel.workspaceId, channel.channelId)
                return (
                  <label
                    key={key}
                    className="flex min-w-0 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:border-fuchsia-200 hover:text-slate-950"
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-slate-300 text-fuchsia-600 focus:ring-fuchsia-500"
                      checked={Boolean(selectedSubscriptions[key])}
                      onChange={() => toggleChannel(channel)}
                      disabled={disabled}
                    />
                    <Hash className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
                    <span className="truncate">{channel.label}</span>
                  </label>
                )
              })}
            </div>
          ) : channelsQuery.isLoading ? (
            <PipedreamLoadingState label="Loading Slack channels..." />
          ) : (
            <p className="mt-3 text-sm text-slate-600">No Slack channels were found.</p>
          )}
          <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-slate-600">
              {selectedCount} {selectedCount === 1 ? 'channel' : 'channels'} selected
            </p>
            <button
              type="button"
              className="inline-flex items-center justify-center gap-2 rounded-md border border-fuchsia-200 bg-fuchsia-50 px-3 py-2 text-sm font-semibold text-fuchsia-800 transition hover:bg-fuchsia-100 disabled:opacity-60"
              onClick={() => onSave(Object.values(selectedSubscriptions))}
              disabled={disabled || !hasChanges}
            >
              {pendingSlackAction === 'save' ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Save className="h-4 w-4" aria-hidden="true" />
              )}
              Save channels
            </button>
          </div>
        </section>
      ) : null}
    </div>
  )
}

export function SlackSummaryCell({ app }: { app: AgentSlackApp }) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <SlackIconTile />
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-slate-900">Slack</p>
        <p className="mt-0.5 line-clamp-2 text-sm text-slate-500">
          {app.connected
            ? app.subscribed
              ? `${app.activeSubscriptionCount} ${app.activeSubscriptionCount === 1 ? 'channel' : 'channels'} subscribed`
              : 'Connected. Choose channels for this agent.'
            : 'Connect Slack and subscribe this agent to selected channels.'}
        </p>
      </div>
    </div>
  )
}

export function AgentSlackAppRowItem({
  app,
  pendingSlackAction,
  disabled,
  onConnect,
  onConfigure,
}: {
  app: AgentSlackApp
  pendingSlackAction: PendingSlackAction
  disabled: boolean
  onConnect: () => void
  onConfigure: () => void
}) {
  return (
    <div className="px-4 py-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_8rem_8rem] sm:items-start">
        <SlackSummaryCell app={app} />
        <div>
          {app.connected ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              Connected
            </span>
          ) : (
            <span className="inline-flex rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-500">
              Workspace
            </span>
          )}
        </div>
        <div className="flex justify-start md:justify-end">
          <button
            type="button"
            className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-fuchsia-200 hover:text-fuchsia-800 disabled:opacity-60"
            onClick={onConfigure}
            disabled={disabled}
          >
            <Settings className="h-4 w-4" aria-hidden="true" />
            Configure
          </button>
        </div>
        <div className="flex justify-start md:justify-end">
          {app.connected ? null : (
            <button
              type="button"
              className="inline-flex min-w-28 items-center justify-center gap-2 rounded-md border border-fuchsia-200 bg-fuchsia-50 px-3 py-2 text-sm font-semibold text-fuchsia-800 transition hover:bg-fuchsia-100 disabled:opacity-60"
              onClick={onConnect}
              disabled={disabled}
            >
              {pendingSlackAction === 'connect' ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Plug className="h-4 w-4" aria-hidden="true" />
              )}
              Connect
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function SlackIconTile() {
  return (
    <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700">
      <img src="/static/images/integrations/native/slack.svg" alt="" className="h-6 w-6 object-contain" loading="lazy" />
    </span>
  )
}

function slackSubscriptionKey(workspaceId: string, channelId: string): string {
  return `${workspaceId}:${channelId}`
}

function activeSlackSelections(app: AgentSlackApp): Record<string, SlackSubscriptionSelection> {
  return Object.fromEntries(
    app.subscriptions
      .filter((subscription) => subscription.status === 'active')
      .map((subscription) => [
        slackSubscriptionKey(subscription.workspaceId, subscription.channelId),
        {
          workspaceId: subscription.workspaceId,
          channelId: subscription.channelId,
          channelName: subscription.channelName,
          channelType: subscription.channelType,
        },
      ]),
  )
}
