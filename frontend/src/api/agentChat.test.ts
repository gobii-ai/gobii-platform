import { beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveSpawnRequest } from './agentChat'

describe('resolveSpawnRequest', () => {
  beforeEach(() => {
    document.cookie = 'csrftoken=test-token'
  })

  it('includes the CSRF header for pending action mutations', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          message: 'ok',
          pending_human_input_requests: [],
          pending_action_requests: [],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await resolveSpawnRequest('/console/api/agents/agent-1/spawn-requests/request-1/decision/', {
      decision: 'approve',
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, init] = fetchMock.mock.calls[0]
    expect(init?.method).toBe('POST')
    expect(init?.credentials).toBe('same-origin')
    const headers = new Headers(init?.headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('X-CSRFToken')).toBe('test-token')
  })
})
