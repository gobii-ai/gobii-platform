import type { ToolDetailProps } from '../../tooling/types'
import { Section } from '../shared'
import { extractBrightDataArray } from '../../../tooling/brightdata'
import { isNonEmptyString } from '../utils'

function toText(value: unknown): string | null {
  return isNonEmptyString(value) ? (value as string) : null
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[, ]+/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function shorten(value: string | null, max = 520): string | null {
  if (!value) return null
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

type RedditPost = {
  title: string | null
  text: string | null
  author: string | null
  community: string | null
  url: string | null
  posted: string | null
  upvotes: number | null
  comments: number | null
}

function normalizeRedditPosts(result: unknown): RedditPost[] {
  const records = extractBrightDataArray(result)
  return records
    .map((record) => {
      return {
        title: toText(record.title),
        text: toText(record.text) || toText(record.description) || toText(record.description_markdown),
        author: toText(record.author) || toText(record.user_posted),
        community: toText(record.community_name) || toText(record.subreddit) || toText(record.community),
        url: toText(record.url) || toText(record.post_url),
        posted: toText(record.date_posted) || toText(record.timestamp),
        upvotes: toNumber(record.num_upvotes ?? record.upvotes ?? record.score),
        comments: toNumber(record.num_comments),
      }
    })
    .filter((item) => item.title || item.text || item.url)
}

export function RedditPostsDetail({ entry }: ToolDetailProps) {
  const posts = normalizeRedditPosts(entry.result).slice(0, 6)

  if (!posts.length) {
    return <p className="text-sm text-slate-500">No posts returned.</p>
  }

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <Section title="Posts">
        <div className="space-y-3">
          {posts.map((post, idx) => {
            const metaParts = [post.community ? `r/${post.community}` : null, post.author ? `u/${post.author}` : null, post.posted].filter(
              Boolean,
            )
            const statsParts = [
              post.upvotes !== null ? `${post.upvotes.toLocaleString()} upvote${post.upvotes === 1 ? '' : 's'}` : null,
              post.comments !== null ? `${post.comments.toLocaleString()} comment${post.comments === 1 ? '' : 's'}` : null,
            ].filter(Boolean)
            const summary = shorten(post.text)

            return (
              <div key={`${post.url ?? post.title ?? idx}`} className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-slate-900">
                    {post.url ? (
                      <a href={post.url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                        {post.title || post.url}
                      </a>
                    ) : (
                      post.title || 'Reddit post'
                    )}
                  </span>
                </div>
                {metaParts.length ? <p className="text-xs text-slate-500">{metaParts.join(' • ')}</p> : null}
                {statsParts.length ? <p className="text-xs text-slate-500">{statsParts.join(' • ')}</p> : null}
                {summary ? <p className="mt-2 whitespace-pre-wrap leading-relaxed text-slate-700">{summary}</p> : null}
              </div>
            )
          })}
        </div>
      </Section>
    </div>
  )
}
