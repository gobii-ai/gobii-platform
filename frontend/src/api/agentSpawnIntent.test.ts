import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchAgentSpawnIntent } from './agentSpawnIntent'

describe('fetchAgentSpawnIntent', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('accepts template recommendations from the spawn intent payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          charter: null,
          charter_override: null,
          preferred_llm_tier: null,
          selected_pipedream_app_slugs: [],
          onboarding_target: null,
          requires_plan_selection: false,
          template_recommendations: {
            category: 'People',
            categories: ['People', 'Revenue'],
            source: 'category',
            templates: [
              {
                id: 'template-1',
                name: 'Talent Scout',
                tagline: 'Find candidates.',
                description: 'Find candidates.',
                category: 'People',
                templateCode: 'talent-scout',
                templateId: 'template-1',
                templateSource: 'public',
                likeCount: 3,
                isOfficial: true,
              },
            ],
          },
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const payload = await fetchAgentSpawnIntent()

    expect(payload.template_recommendations?.templates[0]?.name).toBe('Talent Scout')
    expect(payload.template_recommendations?.categories).toEqual(['People', 'Revenue'])
    expect(payload.template_recommendations?.templates[0]?.templateCode).toBe('talent-scout')
    expect(payload.template_recommendations?.templates[0]?.templateSource).toBe('public')
  })
})
