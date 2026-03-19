import { useCallback, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, AlertCircle, Loader2, RefreshCw } from 'lucide-react'
import {
  fetchSlackSettings,
  saveSlackSettings,
  testSlackConnection,
  type SlackSettingsPayload,
  type SlackTestResult,
} from '../api/agentSlackSettings'

interface Props {
  agentId: string
  slackSettingsUrl: string
  testUrl: string
}

type ThreadPolicy = 'auto' | 'always' | 'never'

interface DraftState {
  workspaceId: string
  channelId: string
  threadPolicy: ThreadPolicy
  isEnabled: boolean
  botToken: string
}

const THREAD_POLICY_OPTIONS: { value: ThreadPolicy; label: string; description: string }[] = [
  {
    value: 'auto',
    label: 'Auto',
    description: 'Reply in thread only if the inbound message was in a thread',
  },
  {
    value: 'always',
    label: 'Always thread',
    description: 'Always reply in a thread, keeping the channel clean',
  },
  {
    value: 'never',
    label: 'Never thread',
    description: 'Always post as a top-level channel message',
  },
]

function draftFromPayload(payload: SlackSettingsPayload): DraftState {
  return {
    workspaceId: payload.workspace_id,
    channelId: payload.channel_id,
    threadPolicy: payload.thread_policy,
    isEnabled: payload.is_enabled,
    botToken: '',
  }
}

export function AgentSlackSettingsScreen({ agentId, slackSettingsUrl, testUrl }: Props) {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['agent-slack-settings', agentId, slackSettingsUrl], [agentId, slackSettingsUrl])

  const { data, isLoading, error } = useQuery<SlackSettingsPayload>({
    queryKey,
    queryFn: () => fetchSlackSettings(slackSettingsUrl),
  })

  const [draft, setDraft] = useState<DraftState | null>(null)
  const [testResult, setTestResult] = useState<SlackTestResult | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)

  // Initialize draft from fetched data
  const activeDraft = useMemo(() => {
    if (draft) return draft
    if (data) return draftFromPayload(data)
    return null
  }, [draft, data])

  const updateDraft = useCallback((updater: (current: DraftState) => DraftState) => {
    setDraft((prev) => {
      const base = prev ?? (data ? draftFromPayload(data) : null)
      if (!base) return prev
      return updater(base)
    })
    setSaveMessage(null)
  }, [data])

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!activeDraft) throw new Error('No draft')
      return saveSlackSettings(slackSettingsUrl, {
        workspace_id: activeDraft.workspaceId,
        channel_id: activeDraft.channelId,
        thread_policy: activeDraft.threadPolicy,
        is_enabled: activeDraft.isEnabled,
        ...(activeDraft.botToken ? { bot_token: activeDraft.botToken } : {}),
      })
    },
    onSuccess: (result) => {
      queryClient.setQueryData(queryKey, result)
      setDraft(null)
      setSaveMessage('Settings saved.')
    },
  })

  const testMutation = useMutation({
    mutationFn: () => testSlackConnection(testUrl),
    onSuccess: (result) => {
      setTestResult(result)
      if (result.ok) {
        queryClient.invalidateQueries({ queryKey })
      }
    },
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-8 w-8 animate-spin text-slate-400" />
      </div>
    )
  }

  if (error || !data || !activeDraft) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        Failed to load Slack settings.
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Slack Integration</h2>
        <p className="mt-1 text-sm text-slate-500">
          Connect this agent to a Slack channel so it can receive and respond to messages.
        </p>
      </div>

      {!data.global_slack_enabled && data.global_slack_disabled_reason && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <strong>Slack is not enabled globally.</strong>{' '}
          {data.global_slack_disabled_reason}
        </div>
      )}

      {/* Bot Token */}
      <div className="rounded-lg border border-slate-200 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-slate-700">Bot Token</h3>
        <p className="text-xs text-slate-500">
          Provide a per-agent Bot User OAuth Token (xoxb-...) or leave blank to use the global token.
        </p>
        <div>
          <input
            type="password"
            placeholder={data.has_bot_token ? '(token set — enter new value to replace)' : 'xoxb-...'}
            value={activeDraft.botToken}
            onChange={(e) => updateDraft((d) => ({ ...d, botToken: e.currentTarget.value }))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
        </div>
      </div>

      {/* Workspace & Channel */}
      <div className="rounded-lg border border-slate-200 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-slate-700">Workspace & Channel</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-sm font-semibold text-slate-700">Workspace ID</label>
            <input
              type="text"
              placeholder="T0123ABCDEF"
              value={activeDraft.workspaceId}
              onChange={(e) => updateDraft((d) => ({ ...d, workspaceId: e.currentTarget.value }))}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="text-sm font-semibold text-slate-700">Channel ID</label>
            <input
              type="text"
              placeholder="C0123ABCDEF"
              value={activeDraft.channelId}
              onChange={(e) => updateDraft((d) => ({ ...d, channelId: e.currentTarget.value }))}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
        </div>
      </div>

      {/* Thread Policy */}
      <div className="rounded-lg border border-slate-200 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-slate-700">Thread Policy</h3>
        <p className="text-xs text-slate-500">
          Controls whether the agent replies inside a Slack thread or as a top-level message.
        </p>
        <div className="space-y-2">
          {THREAD_POLICY_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition ${
                activeDraft.threadPolicy === opt.value
                  ? 'border-indigo-500 bg-indigo-50'
                  : 'border-slate-200 hover:border-slate-300'
              }`}
            >
              <input
                type="radio"
                name="thread_policy"
                value={opt.value}
                checked={activeDraft.threadPolicy === opt.value}
                onChange={() => updateDraft((d) => ({ ...d, threadPolicy: opt.value }))}
                className="mt-0.5"
              />
              <div>
                <span className="text-sm font-medium text-slate-900">{opt.label}</span>
                <p className="text-xs text-slate-500">{opt.description}</p>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* Enable toggle */}
      <div className="rounded-lg border border-slate-200 p-5">
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={activeDraft.isEnabled}
            onChange={(e) => updateDraft((d) => ({ ...d, isEnabled: e.currentTarget.checked }))}
            className="h-4 w-4 rounded border-slate-300 text-indigo-600"
          />
          <span className="text-sm font-semibold text-slate-700">Enable Slack for this agent</span>
        </label>
      </div>

      {/* Connection test */}
      <div className="rounded-lg border border-slate-200 p-5 space-y-3">
        <h3 className="text-sm font-semibold text-slate-700">Test Connection</h3>
        <button
          type="button"
          onClick={() => testMutation.mutate()}
          disabled={testMutation.isPending}
          className="inline-flex items-center gap-2 rounded-lg bg-slate-100 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-50"
        >
          {testMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          Test Connection
        </button>
        {testResult && (
          <div
            className={`flex items-start gap-2 rounded-lg p-3 text-sm ${
              testResult.ok
                ? 'bg-emerald-50 text-emerald-800'
                : 'bg-red-50 text-red-800'
            }`}
          >
            {testResult.ok ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <div>
              {testResult.ok
                ? `Connected to workspace "${testResult.team}" (${testResult.team_id})`
                : testResult.error}
            </div>
          </div>
        )}
        {data.connection_last_ok_at && !testResult && (
          <p className="text-xs text-slate-400">
            Last successful connection: {new Date(data.connection_last_ok_at).toLocaleString()}
          </p>
        )}
        {data.connection_error && !testResult && (
          <p className="text-xs text-red-500">Last error: {data.connection_error}</p>
        )}
      </div>

      {/* Save */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {saveMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save Settings
        </button>
        {saveMessage && (
          <span className="text-sm text-emerald-600">{saveMessage}</span>
        )}
        {saveMutation.error && (
          <span className="text-sm text-red-600">
            {saveMutation.error instanceof Error ? saveMutation.error.message : 'Save failed'}
          </span>
        )}
      </div>
    </div>
  )
}
