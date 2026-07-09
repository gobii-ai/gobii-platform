import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'

const textInputClassName = 'mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:ring-blue-500'
const checkboxClassName = 'h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500'

export type FormFieldProps = {
  id: string
  label: ReactNode
  children: ReactNode
  helpText?: ReactNode
  error?: ReactNode
  className?: string
}

export function FormField({
  id,
  label,
  children,
  helpText,
  error,
  className = '',
}: FormFieldProps) {
  return (
    <div className={className}>
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      {children}
      {helpText ? (
        <p id={`${id}-help`} className="mt-2 text-xs font-normal text-slate-500">
          {helpText}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-xs font-medium text-red-600">
          {error}
        </p>
      ) : null}
    </div>
  )
}

type TextInputProps = InputHTMLAttributes<HTMLInputElement> & {
  id: string
  inputClassName?: string
}

export function TextInput({
  className,
  inputClassName,
  ...props
}: TextInputProps) {
  return (
    <input
      className={[textInputClassName, inputClassName, className].filter(Boolean).join(' ')}
      {...props}
    />
  )
}

type TextareaInputProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  id: string
  inputClassName?: string
}

export function TextareaInput({
  className,
  inputClassName,
  ...props
}: TextareaInputProps) {
  return (
    <textarea
      className={[textInputClassName, inputClassName, className].filter(Boolean).join(' ')}
      {...props}
    />
  )
}

type SelectInputProps = SelectHTMLAttributes<HTMLSelectElement> & {
  id: string
  inputClassName?: string
}

export function SelectInput({
  className,
  inputClassName,
  children,
  ...props
}: SelectInputProps) {
  return (
    <select
      className={[textInputClassName, inputClassName, className].filter(Boolean).join(' ')}
      {...props}
    >
      {children}
    </select>
  )
}

type CheckboxFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  id: string
  label: ReactNode
  helpText?: ReactNode
  containerClassName?: string
  checkboxClassName?: string
}

export function CheckboxField({
  id,
  label,
  helpText,
  containerClassName = 'flex items-center gap-3 rounded-md border border-slate-200 bg-white p-3',
  checkboxClassName: customCheckboxClassName,
  className,
  ...props
}: CheckboxFieldProps) {
  return (
    <div className={containerClassName}>
      <input
        id={id}
        type="checkbox"
        className={[checkboxClassName, customCheckboxClassName, className].filter(Boolean).join(' ')}
        {...props}
      />
      <label htmlFor={id} className="text-sm text-slate-700">
        <span className="font-medium">{label}</span>
        {helpText ? <span className="block text-xs text-slate-500">{helpText}</span> : null}
      </label>
    </div>
  )
}
