/**
 * #243 (frontend half): a search for "webhook" on the integrations page returned only
 * Pipedream catalog apps — the native inbound-webhook feature was invisible on this
 * surface, steering users toward Pipedream.
 */
import { describe, expect, it } from 'vitest'

import { searchSuggestsWebhooks } from './WorkspaceAppsManager'

describe('searchSuggestsWebhooks', () => {
  it('matches webhook-intent searches', () => {
    for (const term of ['webhook', 'webhooks', 'inbound webhook', 'web hook', 'callback url']) {
      expect(searchSuggestsWebhooks(term)).toBe(true)
    }
  })

  it('stays quiet for unrelated searches', () => {
    for (const term of ['slack', 'hubspot', 'web scraping', 'hooks for react']) {
      expect(searchSuggestsWebhooks(term)).toBe(false)
    }
  })
})
