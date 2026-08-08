import type { ReactNode } from 'react'
import type { DiscordEmbed } from '../../types/agentChat'
import { MessageContent } from './MessageContent'

type DiscordEmbedCardProps = {
  embed: DiscordEmbed
  compact?: boolean
  onLinkClick?: (href: string) => boolean | void
}

function safeColor(color?: string): string | undefined {
  return color && /^#[0-9A-Fa-f]{6}$/.test(color) ? color : undefined
}

function ExternalLink({ href, children }: { href?: string; children: ReactNode }) {
  if (!href) return <>{children}</>
  return <a className="text-indigo-600 no-underline hover:underline" href={href} target="_blank" rel="noopener noreferrer">{children}</a>
}

export function DiscordEmbedCard({ embed, compact = false, onLinkClick }: DiscordEmbedCardProps) {
  const color = safeColor(embed.color)
  const fields = embed.fields || []
  const cardClass = `not-prose w-full rounded-md border border-indigo-200/70 border-l-4 text-[13px] leading-[1.35] text-slate-700 ${
    compact ? 'mt-1.5 bg-white/60 px-2.5 py-2' : 'mt-2 max-w-lg bg-white/70 px-3 py-2.5'
  }`

  return (
    <section
      className={cardClass}
      style={color ? { borderLeftColor: color } : undefined}
      data-testid="discord-embed"
    >
      {embed.author ? (
        <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold">
          {embed.author.iconUrl ? <img className="h-4 w-4 rounded-full object-cover" src={embed.author.iconUrl} alt="" loading="lazy" /> : null}
          <ExternalLink href={embed.author.url}>{embed.author.name || embed.author.url}</ExternalLink>
        </div>
      ) : null}
      <div className={embed.thumbnailUrl ? 'flex items-start gap-3' : undefined}>
        <div className="min-w-0 flex-1">
          {embed.provider ? (
            <div className="text-[11px] text-slate-500">
              <ExternalLink href={embed.provider.url}>{embed.provider.name || embed.provider.url}</ExternalLink>
            </div>
          ) : null}
          {embed.title ? (
            <div className="mb-1 font-bold text-slate-900">
              <ExternalLink href={embed.url}>{embed.title}</ExternalLink>
            </div>
          ) : null}
          {embed.description ? (
            <div className={`${compact ? 'line-clamp-3' : ''} [&_p:first-child]:mt-0 [&_p:last-child]:mb-0`}>
              <MessageContent bodyText={embed.description} showEmptyState={false} onLinkClick={onLinkClick} />
            </div>
          ) : null}
          {fields.length ? (
            <div className="mt-2.5 grid grid-cols-1 gap-2.5 sm:grid-cols-3">
              {fields.map((field, index) => (
                <div
                  className={`${field.inline ? '' : 'sm:col-span-3'} min-w-0 ${compact ? 'line-clamp-3' : ''} [&_p:first-child]:mt-0 [&_p:last-child]:mb-0`}
                  key={`${field.name}:${index}`}
                >
                  <div className="mb-0.5 font-semibold text-slate-900">{field.name}</div>
                  <MessageContent bodyText={field.value} showEmptyState={false} onLinkClick={onLinkClick} />
                </div>
              ))}
            </div>
          ) : null}
        </div>
        {embed.thumbnailUrl ? <img className="h-20 w-20 shrink-0 rounded-md object-cover" src={embed.thumbnailUrl} alt="" loading="lazy" /> : null}
      </div>
      {embed.imageUrl ? <img className={`mt-2.5 max-w-full rounded-md object-contain ${compact ? 'max-h-28' : 'max-h-80'}`} src={embed.imageUrl} alt="" loading="lazy" /> : null}
      {embed.videoUrl ? (
        <a className="mt-2 inline-block text-xs font-semibold text-indigo-600 no-underline hover:underline" href={embed.videoUrl} target="_blank" rel="noopener noreferrer">
          Open embedded video
        </a>
      ) : null}
      {embed.footer ? (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-500">
          {embed.footer.iconUrl ? <img className="h-4 w-4 rounded-full object-cover" src={embed.footer.iconUrl} alt="" loading="lazy" /> : null}
          <span>{embed.footer.text}</span>
        </div>
      ) : null}
    </section>
  )
}
