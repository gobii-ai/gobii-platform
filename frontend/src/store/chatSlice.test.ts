import { beforeEach, describe, expect, it, vi } from 'vitest'

import { sendAgentMessage } from '../api/agentChat'
import { fetchAgentSpawnIntent, type AgentSpawnIntent } from '../api/agentSpawnIntent'
import type { TimelineEvent } from '../types/agentChat'
import { createAppStore } from './appStore'
import {
  chatActions,
  loadAgentSpawnIntent,
  selectActiveChatAgentId,
  selectActiveChatSession,
  selectCreateAgentWorkflow,
  sendMessage,
  setAutoScrollPinned,
} from './chatSlice'

vi.mock('../api/agentChat', () => ({
  sendAgentMessage: vi.fn(),
  fetchProcessingStatus: vi.fn(),
}))

vi.mock('../api/agentSpawnIntent', () => ({
  fetchAgentSpawnIntent: vi.fn(),
}))

function makeSpawnIntent(overrides: Partial<AgentSpawnIntent> = {}): AgentSpawnIntent {
  return {
    charter: 'Build a weekly report',
    charter_override: null,
    preferred_llm_tier: 'advanced',
    selected_pipedream_app_slugs: ['slack'],
    onboarding_target: 'agent_ui',
    requires_plan_selection: true,
    ...overrides,
  }
}

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

function selectActiveChatTestSnapshot(state: ReturnType<ReturnType<typeof createAppStore>['getState']>) {
  const session = selectActiveChatSession(state)
  return {
    agentId: selectActiveChatAgentId(state),
    awaitingResponse: session.processing.awaitingResponse,
    pendingEvents: session.timelineUi.pendingEvents,
    insights: session.insights.insightIds.map((id) => session.insights.insightsById[id]).filter(Boolean),
    currentInsightIndex: session.insights.currentInsightIndex,
    sendMessageError: session.workflow.sendMessageError,
    pendingActions: session.workflow.pendingActions,
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

    const state = selectActiveChatTestSnapshot(store.getState())
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

    expect(selectActiveChatTestSnapshot(store.getState()).insights).toEqual([])

    store.dispatch(chatActions.insightsSetForAgent({
      agentId: 'agent-2',
      insights: [makeInsight('fresh-insight', 'Fresh insight')],
    }))

    const state = selectActiveChatTestSnapshot(store.getState())
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
    const pendingEvents = selectActiveChatTestSnapshot(store.getState()).pendingEvents
    expect(pendingEvents).toHaveLength(1)
    expect(pendingEvents[0]).toMatchObject({
      kind: 'message',
      message: {
        bodyText: 'hello backend',
        status: 'sending',
      },
    })
    expect(selectActiveChatTestSnapshot(store.getState()).awaitingResponse).toBe(true)

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

    expect(selectActiveChatTestSnapshot(store.getState())).toMatchObject({
      sendMessageError: 'Unable to send.',
      pendingActions: [{
        id: 'action-1',
        kind: 'human_input',
      }],
    })
  })

  it('loads spawn intent through the thunk and keeps create-agent workflow state serializable', async () => {
    const store = createAppStore()
    vi.mocked(fetchAgentSpawnIntent).mockResolvedValue(makeSpawnIntent())

    const request = store.dispatch(loadAgentSpawnIntent())
    const loadingWorkflow = selectCreateAgentWorkflow(store.getState())
    expect(loadingWorkflow.spawnIntentRequestId).toEqual(expect.any(String))
    expect(loadingWorkflow.spawnIntentStatus).toBe('loading')
    await request.unwrap()
    store.dispatch(chatActions.createAgentDraftMetadataSet({
      body: 'Build a weekly report',
      tier: 'advanced',
      selectedPipedreamAppSlugs: ['slack'],
    }))

    expect(selectCreateAgentWorkflow(store.getState())).toMatchObject({
      spawnIntentStatus: 'ready',
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

  it('marks spawn intent done when the request fails', async () => {
    const store = createAppStore()
    vi.mocked(fetchAgentSpawnIntent).mockRejectedValue(new Error('No intent'))

    await expect(store.dispatch(loadAgentSpawnIntent()).unwrap()).rejects.toThrow('No intent')

    expect(selectCreateAgentWorkflow(store.getState())).toMatchObject({
      spawnIntentStatus: 'done',
      spawnIntent: null,
    })
  })

  it('ignores stale spawn intent results', async () => {
    const store = createAppStore()
    let resolveFirst: (intent: AgentSpawnIntent) => void = () => {}
    let resolveSecond: (intent: AgentSpawnIntent) => void = () => {}
    vi.mocked(fetchAgentSpawnIntent)
      .mockReturnValueOnce(new Promise<AgentSpawnIntent>((resolve) => {
        resolveFirst = resolve
      }))
      .mockReturnValueOnce(new Promise<AgentSpawnIntent>((resolve) => {
        resolveSecond = resolve
      }))

    const firstRequest = store.dispatch(loadAgentSpawnIntent())
    const secondRequest = store.dispatch(loadAgentSpawnIntent())
    resolveSecond(makeSpawnIntent({ charter: 'Second intent' }))
    await secondRequest.unwrap()
    resolveFirst(makeSpawnIntent({ charter: 'First intent' }))
    await firstRequest.unwrap()

    expect(selectCreateAgentWorkflow(store.getState()).spawnIntent?.charter).toBe('Second intent')
  })
})
