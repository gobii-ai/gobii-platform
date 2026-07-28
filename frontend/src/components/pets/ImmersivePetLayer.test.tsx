/**
 * #481: right-clicking the pet and choosing Options navigated the SPA to the profile
 * page, unloading the agent chat the user was in. Options must configure in place.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

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
  useUpdateUserPet: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useDeleteUserPet: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
}))

vi.mock('./PetOptionsPanel', () => ({
  PetOptionsPanel: () => <div data-testid="pet-options-panel" />,
}))

function renderLayer() {
  return render(
    <StoreProvider store={createTestAppStore()}>
      <ImmersivePetLayer />
    </StoreProvider>,
  )
}

describe('ImmersivePetLayer options', () => {
  it('opens pet options in place instead of navigating away from the chat', () => {
    renderLayer()
    const startPath = window.location.pathname

    fireEvent.contextMenu(screen.getByLabelText('Bubbles workspace pet'))
    fireEvent.click(screen.getByText('Options'))

    expect(screen.getByTestId('pet-options-panel')).toBeInTheDocument()
    expect(window.location.pathname).toBe(startPath)
  })

  it('closes the options dialog without touching history', () => {
    renderLayer()

    fireEvent.contextMenu(screen.getByLabelText('Bubbles workspace pet'))
    fireEvent.click(screen.getByText('Options'))
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByTestId('pet-options-panel')).not.toBeInTheDocument()
  })
})
