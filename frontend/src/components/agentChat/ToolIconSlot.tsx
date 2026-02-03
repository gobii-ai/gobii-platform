import { Workflow } from 'lucide-react'

import type { ToolEntryDisplay } from './tooling/types'

type ToolIconSlotProps = {
  entry: ToolEntryDisplay
}

function ToolIcon({ icon, className }: { icon: ToolEntryDisplay['icon'] | undefined; className?: string }) {
  const IconComponent = icon ?? Workflow
  return <IconComponent className={className} aria-hidden="true" />
}

export function ToolIconSlot({ entry }: ToolIconSlotProps) {
  if (entry.status === 'pending') {
    return <span className="tool-chip-spinner tool-chip-spinner--icon" aria-hidden="true" />
  }
  return <ToolIcon icon={entry.icon} className="h-5 w-5" />
}
