import { describe, expect, it } from 'vitest'

import { createAppStore } from './appStore'
import {
  agentResourceStatusActions,
  selectAddonsResourceStatus,
  selectAgentChatUsageSummaryStatus,
  selectQuickSettingsResourceStatus,
} from './agentResourceStatusSlice'

describe('agentResourceStatusSlice', () => {
  it('tracks quick settings and add-ons status by agent id without caching payloads', () => {
    const store = createAppStore()

    store.dispatch(agentResourceStatusActions.quickSettingsLoading('agent-1'))
    store.dispatch(agentResourceStatusActions.quickSettingsLoaded('agent-1'))
    store.dispatch(agentResourceStatusActions.addonsUpdatingSet({ agentId: 'agent-1', updating: true }))

    expect(selectQuickSettingsResourceStatus('agent-1')(store.getState())).toMatchObject({
      status: 'success',
      errorMessage: null,
      updating: false,
    })
    expect(selectAddonsResourceStatus('agent-1')(store.getState())).toMatchObject({
      status: 'idle',
      errorMessage: null,
      updating: true,
    })
  })

  it('tracks agent chat usage summary status', () => {
    const store = createAppStore()

    store.dispatch(agentResourceStatusActions.agentChatUsageSummaryLoading())
    store.dispatch(agentResourceStatusActions.agentChatUsageSummaryFailed('Unable to load usage summary.'))

    expect(selectAgentChatUsageSummaryStatus(store.getState())).toMatchObject({
      status: 'error',
      errorMessage: 'Unable to load usage summary.',
    })
  })
})
