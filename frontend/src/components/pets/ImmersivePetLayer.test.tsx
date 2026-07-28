/**
 * #481 resolution: pet Options performs plain same-tab SPA navigation to the unchanged
 * profile page. The conversation is not lost by leaving — the timeline lives in the
 * store and the composer draft persists per-agent — and no new browsing context is ever
 * opened, so browser tab-vs-window preferences cannot produce a popup window.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ImmersivePetLayer } from './ImmersivePetLayer'
import type { UserPetLibrary } from '../../api/userPets'
import { navigateWithinApp } from '../../util/appNavigation'
import { createTestAppStore, StoreProvider } from '../../test/storeTestUtils'

vi.mock('../../hooks/useIsMobile', () => ({
  useIsMobile: () => false,
}))

vi.mock('../../util/appNavigation', () => ({
  navigateWithinApp: vi.fn(() => true),
}))

const library: UserPetLibrary = {
  pets: [
    {
      id: 'pet-1',
      kind: 'builtin',
      displayName: 'Bubbles',
      description: 'A goldfish',
      spritesheetUrl: 'https://example.test/bubbles.png',
    },
  ],
  preferences: {
    enabled: true,
    selectedPetId: 'pet-1',
    size: 'medium',
    position: null,
  },
  maxCustomPets: 3,
}

vi.mock('../../hooks/useUserPets', () => ({
  useUserPets: () => ({ data: library, isLoading: false, isError: false }),
  useUpdateUserPetPreferences: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
}))

function renderLayer() {
  return render(
    <StoreProvider store={createTestAppStore()}>
      <ImmersivePetLayer />
    </StoreProvider>,
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('ImmersivePetLayer options', () => {
  it('opens the pet panel beside the conversation when inside a chat', () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
    window.history.pushState({}, '', '/app/agents/agent-1')
    renderLayer()

    fireEvent.contextMenu(screen.getByLabelText('Bubbles workspace pet'))
    fireEvent.click(screen.getByText('Options'))

    expect(navigateWithinApp).toHaveBeenCalledWith('/app/agents/agent-1/pet')
    // Never a new browsing context: tab vs window there is browser preference.
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('falls back to the profile section outside a chat', () => {
    window.history.pushState({}, '', '/app/integrations')
    renderLayer()

    fireEvent.contextMenu(screen.getByLabelText('Bubbles workspace pet'))
    fireEvent.click(screen.getByText('Options'))

    expect(navigateWithinApp).toHaveBeenCalledWith('/app/profile#workspace-pet')
  })
})
