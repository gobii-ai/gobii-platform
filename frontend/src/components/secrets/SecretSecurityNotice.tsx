import { ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'

import { embeddedSettingsSurfaceClassName, sharedSettingsGlassFrameClassName } from '../agentSettings/settingsSurfaceClasses'

type SecretSecurityNoticeProps = {
  children: ReactNode
}

export function SecretSecurityNotice({ children }: SecretSecurityNoticeProps) {
  return (
    <div className={`${sharedSettingsGlassFrameClassName} ${embeddedSettingsSurfaceClassName} shadow-none`}>
      <div className="p-4 sm:p-6">
        <div className="flex gap-x-4">
          <div className="flex-shrink-0">
            <ShieldCheck className="h-6 w-6 text-slate-300" />
          </div>
          <div>
            <h3 className="mb-1 text-sm font-semibold text-slate-100">Secure Encryption</h3>
            <p className="text-sm text-slate-300">{children}</p>
          </div>
        </div>
      </div>
    </div>
  )
}
