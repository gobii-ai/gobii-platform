import { describe, expect, it } from 'vitest'

import { createAppStore } from './appStore'
import {
  immersiveShellActions,
  selectImmersiveEmbeddedPanel,
  selectImmersiveGalleryPage,
  selectImmersiveShellSubview,
  selectImmersiveSidebarMode,
} from './immersiveShellSlice'

describe('immersiveShellSlice', () => {
  it('coordinates sidebar mode and embedded shell panel state', () => {
    const store = createAppStore()

    store.dispatch(immersiveShellActions.setSidebarMode('gallery'))
    store.dispatch(immersiveShellActions.setShellSubview('secrets'))
    store.dispatch(immersiveShellActions.setGalleryPage('usage'))

    expect(selectImmersiveSidebarMode(store.getState())).toBe('gallery')
    expect(selectImmersiveShellSubview(store.getState())).toBe('secrets')
    expect(selectImmersiveEmbeddedPanel(store.getState())).toBe('secrets')
    expect(selectImmersiveGalleryPage(store.getState())).toBe('usage')

    store.dispatch(immersiveShellActions.setEmbeddedPanel(null))

    expect(selectImmersiveShellSubview(store.getState())).toBe('chat')
    expect(selectImmersiveEmbeddedPanel(store.getState())).toBeNull()
  })
})
