import type { ToolEntryDisplay } from './tooling/types'

type ToolProviderBadgeProps = {
  entry: ToolEntryDisplay
  className?: string
}

const BRIGHTDATA_SLUG = 'brightdata'

type ProviderInfo = {
  label: string
  slug: string
  title: string
}

function getProviderInfo(entry: ToolEntryDisplay): ProviderInfo | null {
  const serverSlug = entry.mcpInfo?.serverSlug?.toLowerCase()
  if (serverSlug === BRIGHTDATA_SLUG) {
    return {
      label: 'API',
      slug: BRIGHTDATA_SLUG,
      title: 'Bright Data tool',
    }
  }
  const toolName = entry.toolName?.toLowerCase() ?? ''
  if (toolName.startsWith('mcp_brightdata_') || toolName.startsWith('mcp_bright_data_')) {
    return {
      label: 'API',
      slug: BRIGHTDATA_SLUG,
      title: 'Bright Data tool',
    }
  }
  return null
}

export function ToolProviderBadge({ entry, className }: ToolProviderBadgeProps) {
  const provider = getProviderInfo(entry)
  if (!provider) {
    return null
  }
  const classes = ['tool-provider-badge', `tool-provider-badge--${provider.slug}`]
  if (className) {
    classes.push(className)
  }
  return (
    <span className={classes.join(' ')} title={provider.title}>
      {provider.label}
    </span>
  )
}
