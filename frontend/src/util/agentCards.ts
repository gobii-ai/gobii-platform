import type { CSSProperties } from 'react'

type SearchableAgentCard = {
  name?: string | null
  miniDescription?: string | null
  shortDescription?: string | null
  listingDescription?: string | null
  displayTags?: string[] | null
}

export function buildAgentSearchBlob(agent: SearchableAgentCard): string {
  const tags = agent.displayTags?.join(' ') ?? ''
  return [
    agent.name ?? '',
    agent.miniDescription ?? '',
    agent.shortDescription ?? '',
    agent.listingDescription ?? '',
    tags,
  ].join(' ').toLowerCase()
}

export function styleStringToObject(styleString: string): CSSProperties {
  if (!styleString) {
    return {}
  }

  return styleString
    .split(';')
    .map((rule) => rule.trim())
    .filter(Boolean)
    .reduce<CSSProperties | Record<string, string>>((acc, rule) => {
      const [property, value] = rule.split(':')
      if (!property || !value) {
        return acc
      }
      const camelProperty = property.trim().replace(/-([a-z])/g, (_, char) => char.toUpperCase())
      acc[camelProperty as keyof CSSProperties] = value.trim()
      return acc
    }, {})
}
