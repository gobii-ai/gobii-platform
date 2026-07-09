import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { buildLoginUrl } from '../../api/http'
import type {
  NativeIntegrationAccessibleFile,
  NativeIntegrationPickerTokenResponse,
  NativeIntegrationProviderDTO,
} from '../../api/nativeIntegrations'
import {
  buildHomepageNativeIntegrationLoginReturnUrl,
  HomepageIntegrationsModal,
} from './HomepageIntegrationsModal'

const mocks = vi.hoisted(() => ({
  fetchNativeIntegrationPickerToken: vi.fn(),
  openGoogleDrivePicker: vi.fn(),
}))

vi.mock('../../api/nativeIntegrations', async () => {
  const actual = await vi.importActual<typeof import('../../api/nativeIntegrations')>('../../api/nativeIntegrations')
  return {
    ...actual,
    fetchNativeIntegrationPickerToken: mocks.fetchNativeIntegrationPickerToken,
  }
})

vi.mock('../mcp/NativeIntegrationShared', async () => {
  const actual = await vi.importActual<typeof import('../mcp/NativeIntegrationShared')>('../mcp/NativeIntegrationShared')
  return {
    ...actual,
    openGoogleDrivePicker: mocks.openGoogleDrivePicker,
  }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

function createDeferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((innerResolve, innerReject) => {
    resolve = innerResolve
    reject = innerReject
  })
  return { promise, reject, resolve }
}

const googleDriveProvider: NativeIntegrationProviderDTO = {
  provider_key: 'google_drive',
  display_name: 'Google Drive',
  description: 'Grant file access for Google Sheets and Google Docs.',
  auth_type: 'oauth2',
  icon: 'google_drive',
  api_hosts: ['googleapis.com'],
  scopes: ['https://www.googleapis.com/auth/drive.file'],
  connected: true,
  scope: 'user',
  expires_at: null,
  connect_url: '/console/api/native-integrations/google_drive/connect/',
  files_url: '/console/api/native-integrations/google_drive/files/',
  picker_token_url: '/console/api/native-integrations/google_drive/picker-token/',
  agent_event_url: '/console/api/native-integrations/google_drive/agent-events/',
  revoke_url: '/console/api/native-integrations/google_drive/revoke/',
}

function renderHomepageIntegrationsModal() {
  document.body.innerHTML = '<div id="selected-fields"></div>'
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  })
  return render(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(HomepageIntegrationsModal, {
        builtins: [],
        initialSearchTerm: '',
        initialSelectedAppSlugs: [],
        nativeIntegrationsUrl: '',
        nativeProviders: [googleDriveProvider],
        isAuthenticated: true,
        searchUrl: '/console/api/pipedream-apps/search/',
        selectedFieldsContainerId: 'selected-fields',
        initialOpen: true,
        openNativePicker: mocks.openGoogleDrivePicker,
      }),
    ),
  )
}

describe('homepage native integration login redirects', () => {
  it('builds a homepage return URL that reopens the integrations modal for the provider', () => {
    const returnUrl = buildHomepageNativeIntegrationLoginReturnUrl(
      {
        displayName: 'Google Drive',
        providerKey: 'google_drive',
      },
      'https://gobii.test/?utm_source=homepage#apps',
    )

    expect(returnUrl).toBe('/?utm_source=homepage&integration_search=Google+Drive#apps')
  })

  it('preserves the modal return URL as the login next parameter', () => {
    expect(buildLoginUrl('/?integration_search=Google+Drive#apps')).toBe(
      '/accounts/login/?next=%2F%3Fintegration_search%3DGoogle%2BDrive%23apps',
    )
  })

  it('keeps the homepage modal mounted while Google Picker is active and restores scroll afterward', async () => {
    const tokenDeferred = createDeferred<NativeIntegrationPickerTokenResponse>()
    const pickerDeferred = createDeferred<NativeIntegrationAccessibleFile[]>()
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    Object.defineProperty(window, 'scrollX', { configurable: true, value: 12 })
    Object.defineProperty(window, 'scrollY', { configurable: true, value: 640 })
    mocks.fetchNativeIntegrationPickerToken.mockReturnValue(tokenDeferred.promise)
    mocks.openGoogleDrivePicker.mockReturnValue(pickerDeferred.promise)

    renderHomepageIntegrationsModal()

    fireEvent.click(screen.getByRole('button', { name: 'Select Files' }))

    await waitFor(() => {
      expect(scrollTo).toHaveBeenCalledWith(0, 0)
    })
    expect(screen.getByRole('dialog', { name: 'Manage integrations' })).toBeInTheDocument()

    tokenDeferred.resolve({
      accessToken: 'access-token',
      developerKey: 'developer-key',
      appId: 'app-id',
      scope: 'user',
      expiresAt: null,
    })
    await waitFor(() => {
      expect(mocks.openGoogleDrivePicker).toHaveBeenCalledTimes(1)
    })
    expect(screen.getByRole('dialog', { name: 'Manage integrations' })).toBeInTheDocument()

    pickerDeferred.resolve([
      {
        externalId: 'sheet-123',
        name: 'Q2 Sales Tracker',
        mimeType: 'application/vnd.google-apps.spreadsheet',
        webUrl: 'https://docs.google.com/spreadsheets/d/sheet-123/edit',
      },
    ])
    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Manage integrations' })).toBeInTheDocument()
    })
    expect(scrollTo).toHaveBeenLastCalledWith(12, 640)
  })
})
