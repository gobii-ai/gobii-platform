import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

import { useIsMobile } from '../../hooks/useIsMobile'
import { Modal } from './Modal'
import { MobileSheet, type MobileSheetProps } from './MobileSheet'

type ImmersiveDialogMode = 'auto' | 'modal' | 'sheet'

type ImmersiveDialogProps = {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  icon?: LucideIcon | null
  ariaLabel?: string
  children: ReactNode
  mobileChildren?: ReactNode
  footer?: ReactNode
  bodyPadding?: boolean
  tone?: MobileSheetProps['tone']
  mobileBreakpoint?: number
  desktopWidthClass?: string
  desktopBodyClassName?: string
  desktopPanelClassName?: string
  desktopContainerClassName?: string
  desktopIconBgClass?: string
  desktopIconColorClass?: string
  dismissible?: boolean
  forceMode?: ImmersiveDialogMode
}

export function ImmersiveDialog({
  open,
  onClose,
  title,
  subtitle,
  icon,
  ariaLabel,
  children,
  mobileChildren,
  footer,
  bodyPadding = true,
  tone = 'default',
  mobileBreakpoint = 768,
  desktopWidthClass,
  desktopBodyClassName = '',
  desktopPanelClassName = '',
  desktopContainerClassName = '',
  desktopIconBgClass,
  desktopIconColorClass,
  dismissible = true,
  forceMode = 'auto',
}: ImmersiveDialogProps) {
  const isMobile = useIsMobile(mobileBreakpoint)

  if (!open) {
    return null
  }

  const useSheet = forceMode === 'sheet' || (forceMode === 'auto' && isMobile)

  if (useSheet) {
    return (
      <MobileSheet
        open={open}
        onClose={onClose}
        title={title}
        subtitle={subtitle}
        icon={icon}
        ariaLabel={ariaLabel ?? title}
        bodyPadding={bodyPadding}
        tone={tone}
        dismissible={dismissible}
      >
        {mobileChildren ?? (
          <>
            {children}
            {footer ? <div className="mt-auto px-4 pb-5 pt-4">{footer}</div> : null}
          </>
        )}
      </MobileSheet>
    )
  }

  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      widthClass={desktopWidthClass}
      icon={icon}
      iconBgClass={desktopIconBgClass}
      iconColorClass={desktopIconColorClass}
      bodyClassName={desktopBodyClassName}
      containerClassName={desktopContainerClassName}
      panelClassName={desktopPanelClassName}
      footer={footer}
      dismissible={dismissible}
    >
      {children}
    </Modal>
  )
}
