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
  it('opens the unchanged profile page via a target=_blank link, leaving the chat alone', () => {
    // A real anchor click, not window.open: Safari turns window.open into a popup
    // window, and any features string does the same in Chrome. Only a _blank link is
    // reliably a tab everywhere.
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null)
    const clicks: Array<{ href: string; target: string; rel: string }> = []
    const recordClick = (event: MouseEvent) => {
      const anchor = (event.target as Element | null)?.closest?.('a')
      if (anchor) {
        clicks.push({ href: anchor.getAttribute('href') ?? '', target: anchor.target, rel: anchor.rel })
        event.preventDefault()
      }
    }
    document.addEventListener('click', recordClick, true)
    renderLayer()
    const startPath = window.location.pathname

    fireEvent.contextMenu(screen.getByLabelText('Bubbles workspace pet'))
    fireEvent.click(screen.getByText('Options'))

    document.removeEventListener('click', recordClick, true)
    expect(clicks).toEqual([{ href: '/app/profile#workspace-pet', target: '_blank', rel: 'noopener' }])
    expect(openSpy).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe(startPath)
  })
})
