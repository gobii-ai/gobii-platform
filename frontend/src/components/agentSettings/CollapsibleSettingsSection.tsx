import type { ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'

import { SettingsSurface } from '../common/SettingsSurface'

type CollapsibleSettingsSectionProps = {
  id: string
  title: ReactNode
  subtitle?: ReactNode
  children: ReactNode
  bodyClassName?: string
}

export function CollapsibleSettingsSection({
  id,
  title,
  subtitle,
  children,
  bodyClassName = 'px-5 py-5',
}: CollapsibleSettingsSectionProps) {
  return (
    <SettingsSurface as="details" id={id} variant="embedded" shadowClassName="shadow-none" className="group">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
          {subtitle ? <p className="text-sm text-slate-400">{subtitle}</p> : null}
        </div>
        <ChevronDown className="h-4 w-4 text-slate-400 transition-transform duration-200 group-open:-rotate-180" aria-hidden="true" />
      </summary>
      <div className={`border-t border-slate-200/15 ${bodyClassName}`}>{children}</div>
    </SettingsSurface>
  )
}
