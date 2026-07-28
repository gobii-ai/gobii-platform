/**
 * #481: right-clicking the pet and choosing Options navigated the SPA to the profile
 * page, unloading the agent chat the user was in. Options now opens the unchanged
 * profile page in a new tab; the current conversation must not navigate at all.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ImmersivePetLayer } from './ImmersivePetLayer'
import type { UserPetLibrary } from '../../api/userPets'
import { createTestAppStore, StoreProvider } from '../../test/storeTestUtils'

vi.mock('../../hooks/useIsMobile', () => ({
  useIsMobile: () => false,
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
  vi.restoreAllMocks()
})

describe('ImmersivePetLayer options', () => {
  it('opens the unchanged profile page in a new tab, leaving the chat alone', () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
    renderLayer()
    const startPath = window.location.pathname

    fireEvent.contextMenu(screen.getByLabelText('Bubbles workspace pet'))
    fireEvent.click(screen.getByText('Options'))

    expect(openSpy).toHaveBeenCalledWith('/app/profile#workspace-pet', '_blank', 'noopener')
    expect(window.location.pathname).toBe(startPath)
  })
})
