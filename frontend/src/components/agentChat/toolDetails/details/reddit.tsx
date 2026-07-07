import type { ToolDetailProps } from '../../tooling/types'
import { EmptyToolResult, ExternalLinkText, MetaLine, ResultListSection, ToolResultCard } from '../shared'
import { extractBrightDataArray } from '../../../tooling/brightdata'
import { shorten, toNumber, toText } from '../brightDataUtils'

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
    return <EmptyToolResult compact>No posts returned.</EmptyToolResult>
  }

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <ResultListSection title="Posts">
        {posts.map((post, idx) => {
          const metaParts = [post.community ? `r/${post.community}` : null, post.author ? `u/${post.author}` : null, post.posted]
          const statsParts = [
            post.upvotes !== null ? `${post.upvotes.toLocaleString()} upvote${post.upvotes === 1 ? '' : 's'}` : null,
            post.comments !== null ? `${post.comments.toLocaleString()} comment${post.comments === 1 ? '' : 's'}` : null,
          ]
          const summary = shorten(post.text, 520)

          return (
            <ToolResultCard key={`${post.url ?? post.title ?? idx}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-slate-900">
                  {post.url ? (
                    <ExternalLinkText href={post.url}>{post.title || post.url}</ExternalLinkText>
                  ) : (
                    post.title || 'Reddit post'
                  )}
                </span>
              </div>
              <MetaLine items={metaParts} />
              <MetaLine items={statsParts} />
              {summary ? <p className="mt-2 whitespace-pre-wrap leading-relaxed text-slate-700">{summary}</p> : null}
            </ToolResultCard>
          )
        })}
      </ResultListSection>
    </div>
  )
}
