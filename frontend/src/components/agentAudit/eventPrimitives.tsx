import type { LucideIcon } from 'lucide-react'

import { looksLikeHtml, sanitizeHtml } from '../../util/sanitize'

type RenderHtmlOrTextOptions = {
  htmlClassName?: string
  textClassName?: string
}

export function renderHtmlOrText(
  value: string,
  {
    htmlClassName = 'prose prose-sm max-w-none rounded-md bg-white px-3 py-2 text-slate-800 shadow-inner shadow-slate-200/60',
    textClassName = 'whitespace-pre-wrap break-words text-sm text-slate-800',
  }: RenderHtmlOrTextOptions = {},
) {
  if (looksLikeHtml(value)) {
    return <div className={htmlClassName} dangerouslySetInnerHTML={{ __html: sanitizeHtml(value) }} />
  }
  return <div className={textClassName}>{value}</div>
}

export function IconCircle({ icon: Icon, bgClass, textClass }: { icon: LucideIcon; bgClass: string; textClass: string }) {
  return (
    <div className={`mt-0.5 flex h-9 w-9 items-center justify-center rounded-full ${bgClass} ${textClass}`}>
      <Icon className="h-4 w-4" aria-hidden />
    </div>
  )
}

export function TokenPill({ label, value }: { label: string; value: number | null | undefined }) {
  if (value == null) return null
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-1 text-xs font-medium text-slate-800">
      <span className="text-[10px] uppercase tracking-wide text-slate-600">{label}</span>
      <span className="font-semibold">{value}</span>
    </span>
  )
}
