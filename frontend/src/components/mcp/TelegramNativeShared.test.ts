import { describe, expect, it } from 'vitest'

import { telegramAppUrlForWebUrl } from './TelegramNativeShared'

describe('telegramAppUrlForWebUrl', () => {
  it('converts manager start links to Telegram app links', () => {
    expect(telegramAppUrlForWebUrl('https://t.me/GobiiManagerBot?start=abc123')).toBe(
      'tg://resolve?domain=GobiiManagerBot&start=abc123',
    )
  })

  it('converts managed bot creation links to Telegram app links', () => {
    expect(
      telegramAppUrlForWebUrl('https://t.me/newbot/GobiiManagerBot/jean_orbit_bot?name=Jean+Orbit'),
    ).toBe('tg://newbot/GobiiManagerBot/jean_orbit_bot?name=Jean+Orbit')
  })

  it('ignores non-Telegram links', () => {
    expect(telegramAppUrlForWebUrl('https://example.com/newbot')).toBe('')
  })
})
