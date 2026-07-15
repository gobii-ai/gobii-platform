import { Search } from 'lucide-react'

import type { ToolEntryDisplay } from './types'

const TOOL_SEARCH_TOOL_NAMES = new Set(['search_tools', 'search_web', 'web_search', 'search'])

export type ActivityEntryPresentation = {
  label: string
  caption: string | null
  icon?: ToolEntryDisplay['icon']
}

export function deriveActivityEntryPresentation(entry: ToolEntryDisplay): ActivityEntryPresentation {
  if (entry.sourceEntry?.developerEvent) {
    return {
      label: entry.label,
      caption: entry.caption && entry.caption !== entry.label ? entry.caption : null,
      icon: entry.icon,
    }
  }
  const toolName = (entry.toolName ?? '').toLowerCase()
  const isSearch = TOOL_SEARCH_TOOL_NAMES.has(toolName) || entry.label.toLowerCase() === 'tool search'

  if (isSearch) {
    return {
      label: 'Searching tools',
      caption: entry.caption && entry.caption !== entry.label ? entry.caption : null,
      icon: Search,
    }
  }

  return {
    label: entry.label,
    caption: entry.caption && entry.caption !== entry.label ? entry.caption : null,
    icon: entry.icon,
  }
}
