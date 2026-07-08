import { describe, expect, it } from 'vitest'

import { createAppStore } from './appStore'
import {
  agentResourceMirrorsActions,
  selectAddonsMirror,
  selectAgentChatUsageSummaryMirror,
  selectQuickSettingsMirror,
} from './agentResourceMirrorsSlice'

describe('agentResourceMirrorsSlice', () => {
  it('mirrors quick settings and add-ons by agent id', () => {
    const store = createAppStore()

    store.dispatch(agentResourceMirrorsActions.quickSettingsLoading('agent-1'))
    store.dispatch(agentResourceMirrorsActions.quickSettingsLoaded({
      agentId: 'agent-1',
      data: {
        settings: { dailyCredits: null },
        status: { dailyCredits: null },
      },
    }))
    store.dispatch(agentResourceMirrorsActions.addonsUpdatingSet({ agentId: 'agent-1', updating: true }))

    expect(selectQuickSettingsMirror('agent-1')(store.getState())).toMatchObject({
      data: {
        settings: { dailyCredits: null },
        status: { dailyCredits: null },
      },
      status: 'success',
      errorMessage: null,
    })
    expect(selectAddonsMirror('agent-1')(store.getState())).toMatchObject({
      updating: true,
    })
  })

  it('mirrors agent chat usage summary state', () => {
    const store = createAppStore()

    store.dispatch(agentResourceMirrorsActions.agentChatUsageSummaryLoading())
    store.dispatch(agentResourceMirrorsActions.agentChatUsageSummaryFailed('Unable to load usage summary.'))

    expect(selectAgentChatUsageSummaryMirror(store.getState())).toMatchObject({
      status: 'error',
      errorMessage: 'Unable to load usage summary.',
    })
  })
})
