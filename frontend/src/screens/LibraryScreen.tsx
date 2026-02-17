import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { InfiniteData } from '@tanstack/react-query'
import { AlertTriangle, Heart, Library as LibraryIcon, Loader2, Search } from 'lucide-react'

import { fetchLibraryAgents, type LibraryAgentsPayload, toggleLibraryAgentLike } from '../api/library'

type LibraryScreenProps = {
  listUrl: string
  likeUrl: string
  canLike: boolean
}

const MOST_POPULAR_LABEL = 'Most Popular'
const MOST_POPULAR_KEY = '__most_popular__'
const PAGE_SIZE = 24

function categoryChipClassName(isActive: boolean): string {
  if (isActive) {
    return 'whitespace-nowrap rounded-full border border-indigo-600 bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white transition'
  }
  return 'whitespace-nowrap rounded-full border border-indigo-200 bg-white px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:bg-indigo-50'
}

function categorySidebarButtonClassName(isActive: boolean): string {
  if (isActive) {
    return 'flex w-full items-center justify-between rounded-lg border border-indigo-600 bg-indigo-600 px-3 py-2 text-sm font-semibold text-white transition'
  }
  return 'flex w-full items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-indigo-200 hover:bg-indigo-50'
}

function updateLikeInCachedPayload(
  payload: InfiniteData<LibraryAgentsPayload> | undefined,
  update: {
    agentId: string
    likeCount: number
    isLiked: boolean
  },
): InfiniteData<LibraryAgentsPayload> | undefined {
  if (!payload) {
    return payload
  }
  const { agentId, likeCount, isLiked } = update

  let previousLikeCount: number | null = null
  for (const page of payload.pages) {
    const existingAgent = page.agents.find((agent) => agent.id === agentId)
    if (existingAgent) {
      previousLikeCount = existingAgent.likeCount
      break
    }
  }

  if (previousLikeCount === null) {
    return payload
  }

  const delta = likeCount - previousLikeCount
  return {
    ...payload,
    pages: payload.pages.map((page) => ({
      ...page,
      libraryTotalLikes: Math.max(0, page.libraryTotalLikes + delta),
      agents: page.agents.map((agent) => (agent.id === agentId ? { ...agent, likeCount, isLiked } : agent)),
    })),
  }
}

export function LibraryScreen({ listUrl, likeUrl, canLike }: LibraryScreenProps) {
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const normalizedSearchQuery = searchQuery.trim()
  const queryClient = useQueryClient()

  const libraryQuery = useInfiniteQuery({
    queryKey: ['library-agents', listUrl, selectedCategory ?? MOST_POPULAR_KEY, normalizedSearchQuery || '__all__'],
    queryFn: ({ signal, pageParam }) =>
      fetchLibraryAgents(listUrl, {
        signal,
        offset: pageParam,
        limit: PAGE_SIZE,
        category: selectedCategory,
        query: normalizedSearchQuery || null,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => (lastPage.hasMore ? lastPage.offset + lastPage.limit : undefined),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const likeMutation = useMutation({
    mutationFn: (agentId: string) => toggleLibraryAgentLike(likeUrl, agentId),
    onSuccess: (result) => {
      queryClient.setQueriesData<InfiniteData<LibraryAgentsPayload>>(
        { queryKey: ['library-agents', listUrl] },
        (payload) =>
          updateLikeInCachedPayload(payload, {
            agentId: result.agentId,
            likeCount: result.likeCount,
            isLiked: result.isLiked,
          }),
      )
      void queryClient.invalidateQueries({ queryKey: ['library-agents', listUrl] })
    },
  })

  const pages = libraryQuery.data?.pages ?? []
  const firstPage = pages[0]
  const agents = pages.flatMap((page) => page.agents)
  const topCategories = firstPage?.topCategories ?? []
  const totalAgents = firstPage?.totalAgents ?? 0
  const libraryTotalAgents = firstPage?.libraryTotalAgents ?? totalAgents
  const libraryTotalLikes = firstPage?.libraryTotalLikes ?? 0
  const hasMore = Boolean(libraryQuery.hasNextPage)

  const categoryFilters = useMemo(() => topCategories, [topCategories])

  useEffect(() => {
    if (!selectedCategory || categoryFilters.length === 0) {
      return
    }
    const validCategories = new Set(categoryFilters.map((item) => item.name))
    if (!validCategories.has(selectedCategory)) {
      setSelectedCategory(null)
    }
  }, [categoryFilters, selectedCategory])

  if (libraryQuery.isPending) {
    return (
      <div className="gobii-card-base flex min-h-[50vh] items-center justify-center px-6 py-10">
        <div className="inline-flex items-center gap-3 rounded-full border border-indigo-100 bg-indigo-50 px-4 py-2 text-indigo-700">
          <Loader2 className="size-5 animate-spin" aria-hidden="true" />
          <span className="text-sm font-semibold">Loading shared agents...</span>
        </div>
      </div>
    )
  }

  if (libraryQuery.isError) {
    const errorMessage = libraryQuery.error instanceof Error ? libraryQuery.error.message : 'Unable to load the library right now.'
    return (
      <div className="gobii-card-base border border-red-200 p-6">
        <div className="flex items-start gap-3 text-red-700">
          <AlertTriangle className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold">Library unavailable</p>
            <p className="mt-1 text-sm">{errorMessage}</p>
          </div>
        </div>
      </div>
    )
  }

  const isMostPopularSelected = selectedCategory === null
  const isSearchActive = normalizedSearchQuery.length > 0
  const emptyHeading = isSearchActive
    ? `No shared agents match "${normalizedSearchQuery}".`
    : selectedCategory
      ? 'No shared agents found in this category.'
      : 'No shared agents found right now.'
  const emptyDescription = isSearchActive
    ? 'Try another keyword or clear search.'
    : selectedCategory
      ? 'Try another category.'
      : 'Check back soon for newly shared agents.'

  return (
    <div className="space-y-6 pb-10">
      <section className="gobii-card-base px-6 py-6 sm:px-8">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-700">Discover</p>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">Most popular shared Gobii agents</h1>
            <p className="max-w-3xl text-sm text-slate-600 sm:text-base">
              Browse publicly shared agents from across Gobii.
            </p>
            {!canLike ? (
              <p className="text-sm font-medium text-indigo-700">Sign in to like templates.</p>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-100 bg-indigo-50 px-4 py-2 text-sm font-semibold text-indigo-800">
              <LibraryIcon className="size-4" aria-hidden="true" />
              {libraryTotalAgents} shared agents
            </div>
            <div className="inline-flex items-center gap-2 rounded-full border border-rose-100 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-700">
              <Heart className="size-4 fill-rose-500 text-rose-500" aria-hidden="true" />
              {libraryTotalLikes} likes
            </div>
          </div>
        </div>
      </section>

      <section className="space-y-3 md:hidden">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700">Search</p>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-indigo-400" aria-hidden="true" />
            <input
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.currentTarget.value)}
              placeholder="Search agents..."
              className="w-full rounded-lg border border-indigo-200 bg-white py-2.5 pl-9 pr-3 text-base text-slate-800 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              autoComplete="off"
            />
          </div>
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700">Most Popular</p>
          <button
            type="button"
            onClick={() => setSelectedCategory(null)}
            className={categoryChipClassName(isMostPopularSelected)}
          >
            {MOST_POPULAR_LABEL}
          </button>
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700">Categories</p>
          <div className="flex gap-2 overflow-x-auto pb-2">
            {categoryFilters.map((category) => {
              const isActive = selectedCategory === category.name
              return (
                <button
                  key={category.name}
                  type="button"
                  onClick={() => setSelectedCategory(category.name)}
                  className={categoryChipClassName(isActive)}
                >
                  {category.name} ({category.count})
                </button>
              )
            })}
          </div>
        </div>
      </section>

      <section className="grid gap-6 md:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className="hidden md:block">
          <div className="gobii-card-base sticky top-24 space-y-4 p-4">
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700">Search</p>
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-indigo-400" aria-hidden="true" />
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.currentTarget.value)}
                  placeholder="Search agents..."
                  className="w-full rounded-lg border border-indigo-200 bg-white py-2.5 pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  autoComplete="off"
                />
              </div>
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700">Most Popular</p>
              <button
                type="button"
                onClick={() => setSelectedCategory(null)}
                className={categorySidebarButtonClassName(isMostPopularSelected)}
              >
                <span>{MOST_POPULAR_LABEL}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs ${isMostPopularSelected ? 'bg-indigo-500 text-white' : 'bg-indigo-50 text-indigo-700'}`}>
                  {libraryTotalAgents}
                </span>
              </button>
            </div>

            <div>
              <p className="mb-3 text-xs font-semibold uppercase tracking-[0.12em] text-indigo-700">Categories</p>
              <div className="space-y-2">
                {categoryFilters.map((category) => {
                  const isActive = selectedCategory === category.name
                  return (
                    <button
                      key={category.name}
                      type="button"
                      onClick={() => setSelectedCategory(category.name)}
                      className={categorySidebarButtonClassName(isActive)}
                    >
                      <span>{category.name}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs ${isActive ? 'bg-indigo-500 text-white' : 'bg-indigo-50 text-indigo-700'}`}>
                        {category.count}
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>
          </div>
        </aside>

        <div className="space-y-4">
          {agents.length === 0 ? (
            <div className="gobii-card-base p-8 text-center">
              <p className="text-sm font-semibold text-slate-800">{emptyHeading}</p>
              <p className="mt-1 text-sm text-slate-600">{emptyDescription}</p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {agents.map((agent) => {
                  const isLikePending = likeMutation.isPending && likeMutation.variables === agent.id
                  return (
                    <article key={agent.id} className="gobii-card-hoverable group flex h-full flex-col p-5">
                      <div className="mb-3 flex items-start justify-between gap-2">
                        <span className="inline-flex items-center gap-2 text-sm font-medium text-indigo-600">
                          <span className="size-2 rounded-full bg-indigo-400" />
                          {agent.category}
                        </span>
                        {canLike ? (
                          <button
                            type="button"
                            disabled={isLikePending}
                            onClick={() => likeMutation.mutate(agent.id)}
                            className="inline-flex items-center gap-1 rounded-full border border-rose-100 bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-70"
                            aria-label={agent.isLiked ? `Remove like from ${agent.name}` : `Like ${agent.name}`}
                          >
                            {isLikePending ? (
                              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                            ) : (
                              <Heart className={`size-3.5 ${agent.isLiked ? 'fill-rose-500 text-rose-500' : 'text-rose-500'}`} aria-hidden="true" />
                            )}
                            <span>{agent.likeCount}</span>
                          </button>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full border border-rose-100 bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-700">
                            <Heart className="size-3.5 text-rose-500" aria-hidden="true" />
                            <span>{agent.likeCount}</span>
                          </span>
                        )}
                      </div>

                      <p className="text-xs font-medium text-slate-500">@{agent.publicProfileHandle}</p>
                      <h2 className="mt-1 text-base font-semibold text-slate-900 transition group-hover:text-indigo-700">
                        <a href={agent.templateUrl}>{agent.name}</a>
                      </h2>
                      {agent.tagline ? <p className="mt-2 text-sm font-medium text-slate-700">{agent.tagline}</p> : null}

                      <div className="mt-auto pt-4">
                        <a href={agent.templateUrl} className="text-sm font-medium text-indigo-600 transition group-hover:text-indigo-700">
                          View details {'->'}
                        </a>
                      </div>
                    </article>
                  )
                })}
              </div>

              {hasMore ? (
                <div className="pt-2">
                  <button
                    type="button"
                    onClick={() => void libraryQuery.fetchNextPage()}
                    disabled={libraryQuery.isFetchingNextPage}
                    className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm font-semibold text-indigo-700 transition hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-70"
                  >
                    {libraryQuery.isFetchingNextPage ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : null}
                    {libraryQuery.isFetchingNextPage ? 'Loading more...' : 'Load more'}
                  </button>
                </div>
              ) : null}
            </>
          )}
        </div>
      </section>
    </div>
  )
}
