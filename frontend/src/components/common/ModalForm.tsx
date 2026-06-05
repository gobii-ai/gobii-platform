import type { FormEvent, ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

import { Modal } from './Modal'

type ModalFormProps = {
  id: string
  title: string
  subtitle?: string
  icon?: LucideIcon | null
  iconBgClass?: string
  iconColorClass?: string
  widthClass?: string
  bodyClassName?: string
  dismissible?: boolean
  onClose: () => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  children: ReactNode
  submitLabel: string
  submittingLabel?: string
  submitting?: boolean
  submitDisabled?: boolean
  cancelLabel?: string
  errorMessages?: string[] | null
  autoComplete?: string
  formClassName?: string
}

const primaryButtonClassName = 'inline-flex w-full justify-center rounded-md border border-transparent bg-blue-600 px-4 py-2 text-base font-medium text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60 sm:ml-3 sm:w-auto sm:text-sm'
const secondaryButtonClassName = 'inline-flex w-full justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-base font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:opacity-60 sm:ml-3 sm:w-auto sm:text-sm'

export function ModalForm({
  id,
  title,
  subtitle,
  icon,
  iconBgClass,
  iconColorClass,
  widthClass,
  bodyClassName,
  dismissible,
  onClose,
  onSubmit,
  children,
  submitLabel,
  submittingLabel = 'Saving…',
  submitting = false,
  submitDisabled = false,
  cancelLabel = 'Cancel',
  errorMessages = null,
  autoComplete,
  formClassName = 'space-y-4',
}: ModalFormProps) {
  const footer = (
    <>
      <button
        type="submit"
        form={id}
        className={primaryButtonClassName}
        disabled={submitting || submitDisabled}
      >
        {submitting ? submittingLabel : submitLabel}
      </button>
      <button
        type="button"
        className={secondaryButtonClassName}
        onClick={onClose}
        disabled={submitting}
      >
        {cancelLabel}
      </button>
    </>
  )

  return (
    <Modal
      title={title}
      subtitle={subtitle}
      onClose={onClose}
      footer={footer}
      widthClass={widthClass}
      icon={icon}
      iconBgClass={iconBgClass}
      iconColorClass={iconColorClass}
      bodyClassName={bodyClassName}
      dismissible={dismissible}
    >
      <form id={id} onSubmit={onSubmit} className={formClassName} autoComplete={autoComplete}>
        {errorMessages && errorMessages.length > 0 ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-3">
            {errorMessages.map((message) => (
              <p key={message} className="text-sm text-red-700">{message}</p>
            ))}
          </div>
        ) : null}
        {children}
      </form>
    </Modal>
  )
}
