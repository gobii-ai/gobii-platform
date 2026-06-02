import { describe, expect, it } from 'vitest'

import { buildLoginUrl } from '../../api/http'
import { buildHomepageNativeIntegrationLoginReturnUrl } from './HomepageIntegrationsModal'

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
})
