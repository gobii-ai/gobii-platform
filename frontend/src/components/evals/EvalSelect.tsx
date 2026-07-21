import {
  Button,
  ListBox,
  ListBoxItem,
  Popover,
  Select,
  SelectValue,
} from 'react-aria-components'
import { Check, ChevronDown } from 'lucide-react'

export type EvalSelectOption = {
  value: string
  label: string
}

type EvalSelectProps = {
  ariaLabel: string
  value: string
  options: EvalSelectOption[]
  onChange: (value: string) => void
  disabled?: boolean
  className?: string
}

export function EvalSelect({
  ariaLabel,
  value,
  options,
  onChange,
  disabled = false,
  className = '',
}: EvalSelectProps) {
  const emptyOptionKey = '__eval-empty-option__'
  const selectedKey = value || emptyOptionKey

  return (
    <Select
      aria-label={ariaLabel}
      selectedKey={selectedKey}
      isDisabled={disabled || options.length === 0}
      onSelectionChange={(key) => onChange(key === emptyOptionKey ? '' : String(key))}
    >
      <Button
        className={`group inline-flex h-10 min-w-40 items-center justify-between gap-3 rounded-lg border border-slate-300 bg-white px-3 text-left text-sm font-medium text-slate-700 transition-colors hover:border-slate-400 hover:text-slate-900 focus:outline-none focus-visible:border-blue-500 focus-visible:ring-2 focus-visible:ring-blue-500/25 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      >
        <SelectValue className="truncate" />
        <ChevronDown className="h-4 w-4 shrink-0 text-slate-400 transition-transform group-data-[pressed]:rotate-180" />
      </Button>
      <Popover placement="bottom start" offset={6} className="z-50 min-w-[var(--trigger-width)] rounded-lg border border-slate-200 bg-white p-1.5 shadow-lg">
        <ListBox className="max-h-72 overflow-y-auto outline-none">
          {options.map((option) => (
            <ListBoxItem
              key={option.value || emptyOptionKey}
              id={option.value || emptyOptionKey}
              textValue={option.label}
              className="group flex cursor-pointer items-center justify-between gap-3 rounded-md px-3 py-2 text-sm text-slate-700 outline-none data-[focused]:bg-blue-50 data-[focused]:text-blue-800 data-[hovered]:bg-blue-50 data-[hovered]:text-blue-800 data-[selected]:font-semibold data-[selected]:text-blue-700"
            >
              {({ isSelected }) => (
                <>
                  <span>{option.label}</span>
                  {isSelected ? <Check className="h-4 w-4 shrink-0" /> : null}
                </>
              )}
            </ListBoxItem>
          ))}
        </ListBox>
      </Popover>
    </Select>
  )
}
