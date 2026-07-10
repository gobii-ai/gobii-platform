import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { HubSpotInsightPanel } from './HubSpotInsightPanel'
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
  saveNativeIntegrationCredentials: vi.fn(),
}))

vi.mock('../../mcp/NativeIntegrationShared', () => ({
  NativeProviderIcon: () => <img src="/static/images/integrations/native/hubspot.svg" alt="" />,
  handleNativeOAuthConnectSuccess: vi.fn(({
    payload,
    provider,
    popup,
    closedMessage,
    onClosed,
  }: {
    payload: { authorizationUrl: string }
    provider: { displayName: string }
    popup: Window | null
    closedMessage?: string
    onClosed?: (message: string) => void
  }) => {
    if (popup && !popup.closed) {
      popup.location.href = payload.authorizationUrl
      popup.focus()
      return
    }
    if (popup?.closed) {
      onClosed?.(closedMessage ?? `Connection window was closed before ${provider.displayName} opened.`)
      return
    }
    window.location.href = payload.authorizationUrl
  }),
  nativeOAuthContextPayload: () => ({ providerKey: 'hubspot' }),
  openGoogleDrivePicker: vi.fn(),
  openNativeOAuthPopup: vi.fn(),
  storePendingNativeOAuth: vi.fn(),
  usesManualNativeIntegrationCredentials: () => false,
  useNativeIntegrationRefreshEffects: vi.fn(),
}))

const hubspotProvider = {
  providerKey: 'hubspot',
  displayName: 'HubSpot',
  description: 'Connect HubSpot for CRM workflows.',
  authType: 'oauth2',
  icon: 'hubspot',
  apiHosts: [],
  scopes: ['oauth', 'crm.objects.contacts.read', 'crm.objects.contacts.write'],
  connected: false,
  scope: 'oauth',
  expiresAt: null,
  connectUrl: '/console/api/native-integrations/hubspot/connect/',
  filesUrl: '',
  pickerTokenUrl: '',
  agentEventUrl: '/console/api/native-integrations/hubspot/agent-events/',
  revokeUrl: '/console/api/native-integrations/hubspot/revoke/',
  credentialFields: [],
  presentCredentialFields: [],
  missingCredentialFields: [],
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
      <HubSpotInsightPanel nativeIntegrationsUrl={nativeIntegrationsUrl} />
    </QueryClientProvider>,
  )
}

describe('HubSpotInsightPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchNativeIntegrations).mockReset()
    vi.mocked(fetchNativeIntegrationPickerToken).mockReset()
    vi.mocked(startNativeIntegrationConnect).mockReset()
    vi.mocked(openGoogleDrivePicker).mockReset()
    vi.mocked(openNativeOAuthPopup).mockReset()
  })

  it('starts HubSpot OAuth when disconnected', async () => {
    const popup = {
      closed: false,
      focus: vi.fn(),
      location: { href: '' },
    } as unknown as Window
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [{ ...hubspotProvider, connected: false }],
    })
    vi.mocked(openNativeOAuthPopup).mockReturnValue(popup)
    vi.mocked(startNativeIntegrationConnect).mockResolvedValue({
      providerKey: 'hubspot',
      authorizationUrl: 'https://app.hubspot.com/oauth/authorize',
      state: 'state-1',
      expiresAt: '2026-01-01T00:00:00Z',
    })

    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Connect' }))

    await waitFor(() => {
      expect(startNativeIntegrationConnect).toHaveBeenCalledWith(hubspotProvider.connectUrl)
    })
    expect(openNativeOAuthPopup).toHaveBeenCalled()
    expect((popup.location as Location).href).toBe('https://app.hubspot.com/oauth/authorize')
  })

  it('renders connected HubSpot status without picker actions', async () => {
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [{ ...hubspotProvider, connected: true }],
    })

    renderPanel()

    expect(await screen.findByText('HubSpot connected')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Select files' })).not.toBeInTheDocument()
    expect(fetchNativeIntegrationPickerToken).not.toHaveBeenCalled()
    expect(openGoogleDrivePicker).not.toHaveBeenCalled()
  })

  it('shows unavailable setup copy without a native integrations URL', () => {
    renderPanel(null)

    expect(screen.getByText('HubSpot setup is unavailable in this workspace.')).toBeInTheDocument()
  })

  it('shows a configuration message when HubSpot is missing', async () => {
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [],
    })

    renderPanel()

    expect(await screen.findByText('HubSpot is not configured.')).toBeInTheDocument()
  })
})
