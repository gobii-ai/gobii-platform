import { describe, expect, it } from 'vitest'

import { sanitizeHtml } from './sanitize'

describe('sanitizeHtml style-tag handling (bug #504)', () => {
  it('drops <style> blocks entirely instead of leaking their CSS as text', () => {
    const emailHtml = '<style>.btn-14-4 a { padding: 4px !important; } .w-2p { width: 2% !important; }</style><p>You have a new message</p>'
    const out = sanitizeHtml(emailHtml)
    expect(out).toContain('You have a new message')
    expect(out).not.toContain('!important')
    expect(out).not.toContain('.btn-14-4')
  })

  it('drops <title> and head content the same way', () => {
    const out = sanitizeHtml('<title>Reddit Digest</title><p>body</p>')
    expect(out).toContain('body')
    expect(out).not.toContain('Reddit Digest')
  })
})
