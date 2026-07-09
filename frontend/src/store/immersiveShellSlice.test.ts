import { describe, expect, it } from 'vitest'

import { createAppStore } from './appStore'
import {
  immersiveShellActions,
  selectImmersiveShellSubview,
  selectImmersiveSidebarMode,
} from './immersiveShellSlice'

describe('immersiveShellSlice', () => {
  it('coordinates sidebar mode and embedded shell panel state', () => {
    const store = createAppStore()

    store.dispatch(immersiveShellActions.setSidebarMode('gallery'))
    store.dispatch(immersiveShellActions.setShellSubview('secrets'))

    expect(selectImmersiveSidebarMode(store.getState())).toBe('gallery')
    expect(selectImmersiveShellSubview(store.getState())).toBe('secrets')
  })
})
