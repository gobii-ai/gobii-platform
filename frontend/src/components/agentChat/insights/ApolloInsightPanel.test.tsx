import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApolloInsightPanel } from './ApolloInsightPanel'
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
  NativeProviderIcon: () => <img src="/static/images/integrations/native/apollo.svg" alt="" />,
  nativeOAuthContextPayload: () => ({ providerKey: 'apollo' }),
  openGoogleDrivePicker: vi.fn(),
  openNativeOAuthPopup: vi.fn(),
  storePendingNativeOAuth: vi.fn(),
  useNativeIntegrationRefreshEffects: vi.fn(),
}))

const apolloProvider = {
  providerKey: 'apollo',
  displayName: 'Apollo',
  description: 'Connect Apollo for lead sourcing.',
  authType: 'oauth2',
  icon: 'apollo',
  apiHosts: ['api.apollo.io'],
  scopes: ['read_user_profile', 'contacts_search', 'person_read'],
  connected: false,
  scope: 'personal',
  expiresAt: null,
  connectUrl: '/console/api/native-integrations/apollo/connect/',
  filesUrl: '',
  pickerTokenUrl: '',
  revokeUrl: '/console/api/native-integrations/apollo/revoke/',
}

function renderPanel(nativeIntegrationsUrl: string | null = '/console/api/native-integrations/') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ApolloInsightPanel nativeIntegrationsUrl={nativeIntegrationsUrl} />
    </QueryClientProvider>,
  )
}

describe('ApolloInsightPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchNativeIntegrations).mockReset()
    vi.mocked(fetchNativeIntegrationPickerToken).mockReset()
    vi.mocked(startNativeIntegrationConnect).mockReset()
    vi.mocked(openGoogleDrivePicker).mockReset()
    vi.mocked(openNativeOAuthPopup).mockReset()
  })

  it('starts Apollo OAuth when disconnected', async () => {
    const popup = {
      closed: false,
      focus: vi.fn(),
      location: { href: '' },
    } as unknown as Window
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [{ ...apolloProvider, connected: false }],
    })
    vi.mocked(openNativeOAuthPopup).mockReturnValue(popup)
    vi.mocked(startNativeIntegrationConnect).mockResolvedValue({
      providerKey: 'apollo',
      authorizationUrl: 'https://app.apollo.io/#/oauth/authorize',
      state: 'state-1',
      expiresAt: '2026-01-01T00:00:00Z',
    })

    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }))

    await waitFor(() => {
      expect(startNativeIntegrationConnect).toHaveBeenCalledWith(apolloProvider.connectUrl)
    })
    expect(openNativeOAuthPopup).toHaveBeenCalled()
    expect((popup.location as Location).href).toBe('https://app.apollo.io/#/oauth/authorize')
  })

  it('renders connected Apollo status without picker actions', async () => {
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [{ ...apolloProvider, connected: true }],
    })

    renderPanel()

    expect(await screen.findByText('Apollo connected')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Select files' })).not.toBeInTheDocument()
    expect(fetchNativeIntegrationPickerToken).not.toHaveBeenCalled()
    expect(openGoogleDrivePicker).not.toHaveBeenCalled()
  })

  it('shows unavailable setup copy without a native integrations URL', () => {
    renderPanel(null)

    expect(screen.getByText('Apollo setup is unavailable in this workspace.')).toBeInTheDocument()
  })

  it('shows a configuration message when Apollo is missing', async () => {
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [],
    })

    renderPanel()

    expect(await screen.findByText('Apollo is not configured.')).toBeInTheDocument()
  })
})
