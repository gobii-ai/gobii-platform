import { beforeEach, describe, expect, it, vi } from 'vitest'

import { sendAgentMessage } from '../api/agentChat'
import type { TimelineEvent } from '../types/agentChat'
import { createAppStore } from './appStore'
import {
  chatActions,
  selectActiveChatStoreSnapshot,
  selectCreateAgentWorkflow,
  sendMessage,
  setAutoScrollPinned,
} from './chatSlice'

vi.mock('../api/agentChat', () => ({
  sendAgentMessage: vi.fn(),
  fetchProcessingStatus: vi.fn(),
}))

function makeInsight(insightId: string, title: string) {
  return {
    insightId,
    insightType: 'burn_rate' as const,
    priority: 5,
    title,
    body: `${title} body`,
    metadata: {
      agentName: 'Agent',
      todayUsage: { used: 1, limit: 3, percentUsed: 33, unlimited: false },
      monthUsage: { used: 2, limit: 10, percentUsed: 20, unlimited: false },
    },
    dismissible: true,
  }
}

describe('chatSlice insights', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('applies insight query results for the current agent', () => {
    const store = createAppStore()
    store.dispatch(chatActions.agentSelected({ agentId: 'agent-1' }))
    store.dispatch(chatActions.insightsSetForAgent({
      agentId: 'agent-1',
      insights: [makeInsight('insight-1', 'First insight')],
    }))

    const state = selectActiveChatStoreSnapshot(store.getState())
    expect(state.insights).toEqual([makeInsight('insight-1', 'First insight')])
    expect(state.currentInsightIndex).toBe(0)
  })

  it('ignores stale insight results after switching agents', () => {
    const store = createAppStore()
    store.dispatch(chatActions.agentSelected({ agentId: 'agent-1' }))
    store.dispatch(chatActions.agentSelected({ agentId: 'agent-2' }))
    store.dispatch(chatActions.insightsSetForAgent({
      agentId: 'agent-1',
      insights: [makeInsight('stale-insight', 'Stale insight')],
    }))

    expect(selectActiveChatStoreSnapshot(store.getState()).insights).toEqual([])

    store.dispatch(chatActions.insightsSetForAgent({
      agentId: 'agent-2',
      insights: [makeInsight('fresh-insight', 'Fresh insight')],
    }))

    const state = selectActiveChatStoreSnapshot(store.getState())
    expect(state.insights).toEqual([makeInsight('fresh-insight', 'Fresh insight')])
    expect(state.agentId).toBe('agent-2')
  })
})

describe('chatSlice message sending', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('adds an optimistic sending message before the backend send resolves', async () => {
    const store = createAppStore()
    let resolveSend: (event: TimelineEvent) => void = () => {}
    vi.mocked(sendAgentMessage).mockReturnValue(new Promise<TimelineEvent>((resolve) => {
      resolveSend = resolve
    }))
    store.dispatch(chatActions.agentSelected({ agentId: 'agent-1' }))
    store.dispatch(setAutoScrollPinned(false))

    const sendResult = store.dispatch(sendMessage({ body: 'hello backend' }))

    expect(sendAgentMessage).toHaveBeenCalledWith('agent-1', 'hello backend', [])
    const pendingEvents = selectActiveChatStoreSnapshot(store.getState()).pendingEvents
    expect(pendingEvents).toHaveLength(1)
    expect(pendingEvents[0]).toMatchObject({
      kind: 'message',
      message: {
        bodyText: 'hello backend',
        status: 'sending',
      },
    })
    expect(selectActiveChatStoreSnapshot(store.getState()).awaitingResponse).toBe(true)

    resolveSend({
      kind: 'message',
      cursor: 'server-message-cursor',
      message: {
        id: 'server-message',
        bodyText: 'hello backend',
        isOutbound: false,
        channel: 'web',
        timestamp: new Date().toISOString(),
        relativeTimestamp: null,
      },
    })
    await sendResult.unwrap()
  })
})

describe('chatSlice workflow state', () => {
  it('stores per-agent send errors and pending actions', () => {
    const store = createAppStore()
    store.dispatch(chatActions.agentSelected({ agentId: 'agent-1' }))

    store.dispatch(chatActions.sendMessageErrorSet({ agentId: 'agent-1', message: 'Unable to send.' }))
    store.dispatch(chatActions.pendingActionsReplaced({
      agentId: 'agent-1',
      pendingActions: [{
        id: 'action-1',
        kind: 'human_input',
        count: 0,
        requests: [],
      }],
    }))

    expect(selectActiveChatStoreSnapshot(store.getState())).toMatchObject({
      sendMessageError: 'Unable to send.',
      pendingActions: [{
        id: 'action-1',
        kind: 'human_input',
      }],
    })
  })

  it('stores serializable create-agent workflow state', () => {
    const store = createAppStore()

    store.dispatch(chatActions.spawnIntentRequestStarted())
    store.dispatch(chatActions.spawnIntentSet({
      charter: 'Build a weekly report',
      charter_override: null,
      preferred_llm_tier: 'advanced',
      selected_pipedream_app_slugs: ['slack'],
      onboarding_target: 'agent_ui',
      requires_plan_selection: true,
    }))
    store.dispatch(chatActions.createAgentDraftMetadataSet({
      body: 'Build a weekly report',
      tier: 'advanced',
      selectedPipedreamAppSlugs: ['slack'],
    }))

    expect(selectCreateAgentWorkflow(store.getState())).toMatchObject({
      spawnIntentRequestId: 1,
      spawnIntentStatus: 'loading',
      spawnIntent: {
        charter: 'Build a weekly report',
        selected_pipedream_app_slugs: ['slack'],
      },
      draftMetadata: {
        body: 'Build a weekly report',
        tier: 'advanced',
        selectedPipedreamAppSlugs: ['slack'],
      },
    })
  })
})
