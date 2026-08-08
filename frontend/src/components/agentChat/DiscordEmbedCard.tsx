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
  return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
}

export function DiscordEmbedCard({ embed, compact = false, onLinkClick }: DiscordEmbedCardProps) {
  const color = safeColor(embed.color)
  const fields = embed.fields || []

  return (
    <section
      className={`discord-embed-card not-prose${compact ? ' discord-embed-card--compact' : ''}`}
      style={color ? { borderLeftColor: color } : undefined}
      data-testid="discord-embed"
    >
      {embed.author ? (
        <div className="discord-embed-card__author">
          {embed.author.iconUrl ? <img src={embed.author.iconUrl} alt="" /> : null}
          <ExternalLink href={embed.author.url}>{embed.author.name || embed.author.url}</ExternalLink>
        </div>
      ) : null}
      <div className={embed.thumbnailUrl ? 'discord-embed-card__with-thumbnail' : undefined}>
        <div className="discord-embed-card__main">
          {embed.provider ? (
            <div className="discord-embed-card__provider">
              <ExternalLink href={embed.provider.url}>{embed.provider.name || embed.provider.url}</ExternalLink>
            </div>
          ) : null}
          {embed.title ? (
            <div className="discord-embed-card__title">
              <ExternalLink href={embed.url}>{embed.title}</ExternalLink>
            </div>
          ) : null}
          {embed.description ? (
            <div className="discord-embed-card__description">
              <MessageContent bodyText={embed.description} showEmptyState={false} onLinkClick={onLinkClick} />
            </div>
          ) : null}
          {fields.length ? (
            <div className="discord-embed-card__fields">
              {fields.map((field, index) => (
                <div
                  className={`discord-embed-card__field${field.inline ? ' discord-embed-card__field--inline' : ''}`}
                  key={`${field.name}:${index}`}
                >
                  <div className="discord-embed-card__field-name">{field.name}</div>
                  <MessageContent bodyText={field.value} showEmptyState={false} onLinkClick={onLinkClick} />
                </div>
              ))}
            </div>
          ) : null}
        </div>
        {embed.thumbnailUrl ? <img className="discord-embed-card__thumbnail" src={embed.thumbnailUrl} alt="" /> : null}
      </div>
      {embed.imageUrl ? <img className="discord-embed-card__image" src={embed.imageUrl} alt="" /> : null}
      {embed.videoUrl ? (
        <a className="discord-embed-card__video" href={embed.videoUrl} target="_blank" rel="noopener noreferrer">
          Open embedded video
        </a>
      ) : null}
      {embed.footer ? (
        <div className="discord-embed-card__footer">
          {embed.footer.iconUrl ? <img src={embed.footer.iconUrl} alt="" /> : null}
          <span>{embed.footer.text}</span>
        </div>
      ) : null}
    </section>
  )
}
