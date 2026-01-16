import type { ToolDetailProps } from '../../tooling/types'
import { KeyValueList, Section } from '../shared'
import { extractBrightDataArray } from '../../../tooling/brightdata'
import { isNonEmptyString } from '../utils'

function toText(value: unknown): string | null {
  return isNonEmptyString(value) ? (value as string) : null
}

function shorten(value: string | null, max = 520): string | null {
  if (!value) return null
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

function formatDate(value: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
}

type Article = {
  headline: string | null
  url: string | null
  author: string | null
  published: string | null
  updated: string | null
  description: string | null
  content: string | null
  topics: string[]
  related: Array<{ title: string | null; url: string | null }>
}

function normalizeArticles(result: unknown): Article[] {
  const records = extractBrightDataArray(result)
  return records
    .map((record) => {
      const related =
        Array.isArray(record.related_articles) && record.related_articles.length
          ? (record.related_articles as Array<Record<string, unknown>>)
              .map((item) => ({
                title: toText(item.article_title ?? item.title),
                url: toText(item.article_url ?? item.url),
              }))
              .filter((item) => item.title || item.url)
          : []

      const topics = Array.isArray(record.topics)
        ? (record.topics as unknown[]).map((t) => toText(t)).filter(isNonEmptyString) as string[]
        : []

      return {
        headline: toText(record.headline) || toText(record.title),
        url: toText(record.url),
        author: toText(record.author) || toText(record.source),
        published: toText(record.publication_date) || toText(record.published_at),
        updated: toText(record.updated_last) || toText(record.updated_at),
        description: toText(record.description),
        content: toText(record.content),
        topics,
        related,
      }
    })
    .filter((article) => article.headline || article.url || article.content)
}

export function ReutersNewsDetail({ entry }: ToolDetailProps) {
  const articles = normalizeArticles(entry.result).slice(0, 6)

  if (!articles.length) {
    return <p className="text-sm text-slate-500">No articles returned.</p>
  }

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <Section title="Articles">
        <div className="space-y-3">
          {articles.map((article, idx) => {
            const published = formatDate(article.published)
            const updated = formatDate(article.updated)
            const metaParts = [article.author, published].filter(Boolean)
            const statsParts = [updated ? `Updated ${updated}` : null].filter(Boolean)
            const summary = shorten(article.description || article.content)

            return (
              <div key={`${article.url ?? article.headline ?? idx}`} className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-slate-900">
                    {article.url ? (
                      <a href={article.url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                        {article.headline || article.url}
                      </a>
                    ) : (
                      article.headline || 'Article'
                    )}
                  </span>
                </div>
                {metaParts.length ? (
                  <p className="text-xs text-slate-500">{metaParts.join(' • ')}</p>
                ) : null}
                {statsParts.length ? (
                  <p className="text-xs text-slate-500">{statsParts.join(' • ')}</p>
                ) : null}
                {article.topics.length ? (
                  <p className="text-xs text-slate-500">Topics: {article.topics.join(', ')}</p>
                ) : null}
                {summary ? <p className="mt-2 leading-relaxed whitespace-pre-wrap text-slate-700">{summary}</p> : null}

                {article.related.length ? (
                  <div className="mt-2">
                    <p className="text-xs font-semibold text-slate-700">Related</p>
                    <ul className="mt-1 space-y-1">
                      {article.related.slice(0, 4).map((item, relatedIdx) => (
                        <li key={`${item.url ?? item.title ?? relatedIdx}`} className="text-xs text-slate-600">
                          {item.url ? (
                            <a href={item.url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                              {item.title || item.url}
                            </a>
                          ) : (
                            item.title
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      </Section>
    </div>
  )
}
