import { useState } from 'react'

import { CircleHelp } from 'lucide-react'
import { Button } from 'react-aria-components'

type InlineInfoTooltipButtonProps = {
  label: string
  description: string
  disabled?: boolean
}

export function InlineInfoTooltipButton({
  label,
  description,
  disabled = false,
}: InlineInfoTooltipButtonProps) {
  const [isPinnedOpen, setIsPinnedOpen] = useState(false)

  return (
    <div
      className="group/tooltip relative shrink-0"
      onMouseLeave={() => setIsPinnedOpen(false)}
    >
      <Button
        aria-label={label}
        aria-expanded={isPinnedOpen}
        className="inline-flex h-6 w-6 items-center justify-center rounded-full text-slate-400 transition hover:bg-white hover:text-slate-600 focus:bg-white focus:text-slate-600"
        isDisabled={disabled}
        onPress={() => setIsPinnedOpen((isOpen) => !isOpen)}
        onBlur={() => setIsPinnedOpen(false)}
      >
        <CircleHelp className="h-3.5 w-3.5" aria-hidden="true" />
      </Button>
      <div
        role="tooltip"
        className={`pointer-events-none absolute right-0 top-full z-50 mt-1.5 w-72 max-w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs leading-5 text-slate-700 shadow-xl transition ${
          isPinnedOpen
            ? 'visible opacity-100'
            : 'invisible opacity-0 group-hover/tooltip:visible group-hover/tooltip:opacity-100 group-focus-within/tooltip:visible group-focus-within/tooltip:opacity-100'
        }`}
      >
        {description}
      </div>
    </div>
  )
}
