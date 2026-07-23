import { isRecord, parseResultObject } from '../../../../util/objectUtils'
import type { ToolDetailProps } from '../../tooling/types'
import { ExternalLinkText, KeyValueList, Section } from '../shared'

const text = (value: unknown) => typeof value === 'string' && value.trim() ? value.trim() : null
const records = (value: unknown) => Array.isArray(value) ? value.filter(isRecord) : []
const channelLabel = (channel: Record<string, unknown>) => {
  const name = text(channel.channel_name)
  return name ? `#${name.replace(/^#/, '')}` : text(channel.channel_id) || 'Unknown channel'
}
function DiscordEntityList({
  items,
  kind,
}: {
  items: Record<string, unknown>[]
  kind: 'server' | 'channel' | 'subscription'
}) {
  if (!items.length) {
    return <p className="text-slate-500">No {kind}s found.</p>
  }
  return (
    <ul className="space-y-2">
      {items.map((item, index) => {
        const isServer = kind === 'server'
        const heading = isServer ? text(item.name) || text(item.guild_name) || 'Unknown server' : channelLabel(item)
        const id = isServer ? text(item.guild_id) || text(item.id) : text(item.channel_id)
        const context = isServer ? null : text(item.guild_name)
        const status = kind === 'subscription' ? text(item.status) : null
        return (
          <li key={`${kind}-${id || index}`} className="flex min-w-0 items-start justify-between gap-3">
            <span className="min-w-0">
              <strong className="block truncate font-semibold text-slate-800">{heading}</strong>
              {context ? <small className="block truncate text-slate-500">{context}</small> : null}
            </span>
            <span className="shrink-0 text-right">
              {status ? <small className="block capitalize text-slate-600">{status}</small> : null}
              {id ? <code className="block text-xs text-slate-500">{id}</code> : null}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export function DiscordToolDetail({ entry }: ToolDetailProps) {
  const result = parseResultObject(entry.result)
  const status = text(result?.status)
  const message = text(result?.message)

  if (status === 'error') {
    return <p className="text-sm font-medium text-rose-700">{message || 'Discord action failed.'}</p>
  }

  if (entry.toolName === 'add_discord_reaction') {
    return (
      <KeyValueList items={[
        { label: 'Reaction', value: text(result?.emoji) || text(entry.parameters?.emoji) || 'Unknown' },
      ]} />
    )
  }

  const action = text(entry.parameters?.action) || 'manage'
  const guilds = records(result?.guilds)
  const channels = records(result?.channels)
  const subscriptions = records(result?.subscriptions)
  const subscription = isRecord(result?.subscription) ? result.subscription : null
  const connectUrl = text(result?.connect_url)
  const inviteUrl = text(result?.bot_invite_url)

  return (
    <div className="space-y-4 text-sm text-slate-600">
      {message && status !== 'success' ? <p className="whitespace-pre-line text-slate-700">{message}</p> : null}
      <KeyValueList items={[
        subscription ? { label: 'Channel', value: channelLabel(subscription) } : null,
        subscription && text(subscription.guild_name) ? { label: 'Server', value: text(subscription.guild_name) } : null,
        subscription && text(subscription.status) ? { label: 'Status', value: text(subscription.status) } : null,
        connectUrl ? { label: 'Setup', value: <ExternalLinkText href={connectUrl}>Connect Discord</ExternalLinkText> } : null,
        inviteUrl ? { label: 'Bot access', value: <ExternalLinkText href={inviteUrl}>Invite the Gobii bot</ExternalLinkText> } : null,
      ]} />
      {guilds.length || action === 'list_guilds' ? (
        <Section title="Servers"><DiscordEntityList items={guilds} kind="server" /></Section>
      ) : null}
      {channels.length || action === 'discover_channels' ? (
        <Section title="Channels"><DiscordEntityList items={channels} kind="channel" /></Section>
      ) : null}
      {subscriptions.length || action === 'list' ? (
        <Section title="Subscriptions"><DiscordEntityList items={subscriptions} kind="subscription" /></Section>
      ) : null}
    </div>
  )
}
