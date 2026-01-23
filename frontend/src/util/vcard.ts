import { slugify } from './slugify'

export type VCardContact = {
  name: string
  email?: string | null
  phone?: string | null
}

type NameParts = {
  given: string
  family: string
}

function escapeVCardValue(value: string): string {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/\r?\n/g, '\\n')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
}

function splitName(name: string): NameParts {
  const trimmed = name.trim()
  if (!trimmed) {
    return { given: 'Agent', family: '' }
  }
  const parts = trimmed.split(/\s+/)
  if (parts.length === 1) {
    return { given: parts[0], family: '' }
  }
  const family = parts.pop() || ''
  const given = parts.join(' ')
  return {
    given,
    family,
  }
}

export function buildVCard(contact: VCardContact): string {
  const safeName = contact.name?.trim() || 'Agent'
  const { given, family } = splitName(safeName)
  const lines: string[] = [
    'BEGIN:VCARD',
    'VERSION:3.0',
    `FN:${escapeVCardValue(safeName)}`,
    `N:${escapeVCardValue(family)};${escapeVCardValue(given)};;;`,
  ]

  const email = contact.email?.trim()
  if (email) {
    lines.push(`EMAIL;TYPE=INTERNET:${escapeVCardValue(email)}`)
  }

  const phone = contact.phone?.trim()
  if (phone) {
    lines.push(`TEL;TYPE=CELL:${escapeVCardValue(phone)}`)
  }

  lines.push('END:VCARD')
  return `${lines.join('\r\n')}\r\n`
}

export function downloadVCard(contact: VCardContact): void {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return
  }

  const vcard = buildVCard(contact)
  const blob = new Blob([vcard], { type: 'text/vcard;charset=utf-8' })
  const url = window.URL.createObjectURL(blob)

  const baseName = slugify(contact.name || 'agent') || 'agent'
  const fileName = `${baseName}-contact.vcf`

  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}
