import { ArrowLeft } from 'lucide-react'

type EmbeddedAgentShellBackButtonProps = {
  onClick?: () => void
  ariaLabel: string
}

export function EmbeddedAgentShellBackButton({
  onClick,
  ariaLabel,
}: EmbeddedAgentShellBackButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-200/25 bg-slate-900/35 text-slate-100 transition-colors hover:border-slate-100/35 hover:bg-slate-900/55 hover:text-white"
      aria-label={ariaLabel}
    >
      <ArrowLeft className="h-4 w-4" aria-hidden="true" />
    </button>
  )
}
