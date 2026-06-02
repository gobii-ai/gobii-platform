import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { GoogleDriveInsightPanel } from './GoogleDriveInsightPanel'
import {
  fetchNativeIntegrationPickerToken,
  fetchNativeIntegrations,
  startNativeIntegrationConnect,
} from '../../../api/nativeIntegrations'
import {
  openGoogleDrivePicker,
  openNativeOAuthPopup,
} from '../../mcp/NativeIntegrationShared'

vi.mock('../../../api/nativeIntegrations', () => ({
  fetchNativeIntegrations: vi.fn(),
  fetchNativeIntegrationPickerToken: vi.fn(),
  startNativeIntegrationConnect: vi.fn(),
}))

vi.mock('../../mcp/NativeIntegrationShared', () => ({
  NativeProviderIcon: () => <img src="/static/images/integrations/native/google_drive.svg" alt="" />,
  nativeOAuthContextPayload: () => ({ providerKey: 'google_drive' }),
  openGoogleDrivePicker: vi.fn(),
  openNativeOAuthPopup: vi.fn(),
  storePendingNativeOAuth: vi.fn(),
  supportsNativeIntegrationPicker: (provider: { providerKey: string; pickerTokenUrl: string }) => (
    provider.providerKey === 'google_drive' && Boolean(provider.pickerTokenUrl)
  ),
  useNativeIntegrationRefreshEffects: vi.fn(),
}))

const googleDriveProvider = {
  providerKey: 'google_drive',
  displayName: 'Google Drive',
  description: 'Grant file access for Google Sheets.',
  authType: 'oauth2',
  icon: 'google_drive',
  apiHosts: ['sheets.googleapis.com'],
  scopes: ['https://www.googleapis.com/auth/drive.file'],
  connected: false,
  scope: 'personal',
  expiresAt: null,
  connectUrl: '/console/api/native-integrations/google_drive/connect/',
  filesUrl: '/console/api/native-integrations/google_drive/files/',
  pickerTokenUrl: '/console/api/native-integrations/google_drive/picker-token/',
  revokeUrl: '/console/api/native-integrations/google_drive/revoke/',
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <GoogleDriveInsightPanel nativeIntegrationsUrl="/console/api/native-integrations/" />
    </QueryClientProvider>,
  )
}

describe('GoogleDriveInsightPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchNativeIntegrations).mockReset()
    vi.mocked(fetchNativeIntegrationPickerToken).mockReset()
    vi.mocked(startNativeIntegrationConnect).mockReset()
    vi.mocked(openGoogleDrivePicker).mockReset()
    vi.mocked(openNativeOAuthPopup).mockReset()
  })

  it('starts Google Drive OAuth when disconnected', async () => {
    const popup = {
      closed: false,
      focus: vi.fn(),
      location: { href: '' },
    } as unknown as Window
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [{ ...googleDriveProvider, connected: false }],
    })
    vi.mocked(openNativeOAuthPopup).mockReturnValue(popup)
    vi.mocked(startNativeIntegrationConnect).mockResolvedValue({
      providerKey: 'google_drive',
      authorizationUrl: 'https://accounts.google.com/oauth',
      state: 'state-1',
      expiresAt: '2026-01-01T00:00:00Z',
    })

    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }))

    await waitFor(() => {
      expect(startNativeIntegrationConnect).toHaveBeenCalledWith(googleDriveProvider.connectUrl)
    })
    expect(openNativeOAuthPopup).toHaveBeenCalled()
    expect((popup.location as Location).href).toBe('https://accounts.google.com/oauth')
  })

  it('opens Google Picker when connected', async () => {
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [{ ...googleDriveProvider, connected: true }],
    })
    vi.mocked(fetchNativeIntegrationPickerToken).mockResolvedValue({
      accessToken: 'token',
      developerKey: 'developer-key',
      appId: 'app-id',
      scope: 'https://www.googleapis.com/auth/drive.file',
      expiresAt: null,
    })
    vi.mocked(openGoogleDrivePicker).mockResolvedValue(1)

    renderPanel()

    expect(await screen.findByText('Google Drive connected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Select files' }))

    await waitFor(() => {
      expect(fetchNativeIntegrationPickerToken).toHaveBeenCalledWith(googleDriveProvider.pickerTokenUrl)
    })
    expect(openGoogleDrivePicker).toHaveBeenCalled()
  })
})
