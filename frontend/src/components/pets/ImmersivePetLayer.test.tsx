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
  it('renders Options as a real _blank link to the unchanged profile page', () => {
    // A real link the user genuinely clicks: scripted opens (window.open, synthetic
    // anchor clicks) get reclassified as popup windows by browser heuristics.
    renderLayer()

    fireEvent.contextMenu(screen.getByLabelText('Bubbles workspace pet'))
    const options = screen.getByRole('menuitem', { name: 'Options' })

    expect(options.tagName).toBe('A')
    expect(options).toHaveAttribute('href', '/app/profile#workspace-pet')
    expect(options).toHaveAttribute('target', '_blank')
    expect(options).toHaveAttribute('rel', 'noopener')
  })
})
