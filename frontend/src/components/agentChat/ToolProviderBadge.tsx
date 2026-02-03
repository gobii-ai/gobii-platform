import type { ToolEntryDisplay } from './tooling/types'

type ToolProviderBadgeProps = {
  entry: ToolEntryDisplay
  className?: string
}

const BRIGHTDATA_SLUG = 'brightdata'

function isBrightDataEntry(entry: ToolEntryDisplay): boolean {
  const serverSlug = entry.mcpInfo?.serverSlug?.toLowerCase()
  if (serverSlug) {
    return serverSlug === BRIGHTDATA_SLUG
  }
  const toolName = entry.toolName?.toLowerCase() ?? ''
  return toolName.startsWith('mcp_brightdata_') || toolName.startsWith('mcp_bright_data_')
}

export function ToolProviderBadge({ entry, className }: ToolProviderBadgeProps) {
  if (!isBrightDataEntry(entry)) {
    return null
  }
  const classes = ['tool-provider-badge', 'tool-provider-badge--brightdata']
  if (className) {
    classes.push(className)
  }
  return (
    <span className={classes.join(' ')} title="Bright Data tool">
      Bright Data
    </span>
  )
}
