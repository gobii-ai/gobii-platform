import {
  Button as AriaButton,
  CalendarCell,
  CalendarGrid,
  Dialog,
  DialogTrigger,
  Heading,
  Popover,
  RangeCalendar,
} from 'react-aria-components'

import type { DateValue } from '@internationalized/date'

import { clampRangeToMax } from './utils'
import type { DateRangeValue } from './types'

type UsageRangeControlsProps = {
  isPickerOpen: boolean
  onOpenChange: (open: boolean) => void
  onCustomRangePress: () => void
  calendarRange: DateRangeValue | null
  effectiveRange: DateRangeValue | null
  onCalendarChange: (range: DateRangeValue | null) => void
  onRangeComplete: (range: DateRangeValue) => void
  onPrevious: () => void
  onNext: () => void
  onResetCurrent: () => void
  hasEffectiveRange: boolean
  hasInitialRange: boolean
  isCurrentSelection: boolean
  isViewingCurrentBilling: boolean
  maxValue?: DateValue | null
}

export function UsageRangeControls(props: UsageRangeControlsProps) {
  const {
    isPickerOpen,
    onOpenChange,
    onCustomRangePress,
    calendarRange,
    effectiveRange,
    onCalendarChange,
    onRangeComplete,
    onPrevious,
    onNext,
    onResetCurrent,
    hasEffectiveRange,
    hasInitialRange,
    isCurrentSelection,
    isViewingCurrentBilling,
    maxValue,
  } = props

  const selection = calendarRange ?? effectiveRange
  const displayRange =
    selection && selection.start && selection.end && maxValue
      ? clampRangeToMax(selection, maxValue)
      : selection
  const secondaryButtonClassName = 'rounded-md border border-slate-200/25 bg-slate-900/35 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-slate-100/35 hover:bg-slate-900/55 hover:text-white disabled:cursor-not-allowed disabled:border-slate-200/10 disabled:bg-slate-950/20 disabled:text-slate-600'
  const primaryButtonClassName = 'rounded-md border border-sky-300/25 bg-sky-900/45 px-3 py-2 text-sm font-medium text-sky-100 transition-colors hover:border-sky-200/40 hover:bg-sky-900/65 disabled:cursor-not-allowed disabled:border-slate-200/10 disabled:bg-slate-950/20 disabled:text-slate-600'
  const customButtonClassName = 'rounded-md border border-slate-200/25 bg-slate-900/35 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-slate-100/35 hover:bg-slate-900/55 hover:text-white'
  const popoverClassName = 'z-50 mt-2 rounded-xl border border-slate-200/20 bg-slate-950 p-0 text-slate-100 shadow-none'
  const calendarNavButtonClassName = 'rounded-md px-2 py-1 text-sm text-slate-300 transition-colors hover:bg-slate-800'
  const calendarGridClassName = 'border-spacing-1 border-separate gap-y-1 text-center text-xs font-medium uppercase text-slate-400'
  const calendarCellClassName = 'm-0.5 flex h-8 w-8 items-center justify-center rounded-md text-sm text-slate-200 transition-colors hover:bg-sky-900/45 data-[disabled]:text-slate-700 data-[focused]:outline data-[focused]:outline-2 data-[focused]:outline-sky-400 data-[selected]:bg-sky-500 data-[selected]:text-white data-[range-selection]:bg-sky-900/55 data-[outside-month]:text-slate-700'

  return (
    <div className="flex flex-1 flex-wrap items-center gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <AriaButton
          className={secondaryButtonClassName}
          isDisabled={!hasEffectiveRange}
          onPress={onPrevious}
        >
          ‹ Previous
        </AriaButton>
        <AriaButton
          className={primaryButtonClassName}
          isDisabled={!hasInitialRange || isCurrentSelection}
          onPress={onResetCurrent}
        >
          Current period
        </AriaButton>
        <AriaButton
          className={secondaryButtonClassName}
          isDisabled={!hasEffectiveRange || isViewingCurrentBilling}
          onPress={onNext}
        >
          Next ›
        </AriaButton>
      </div>
      <DialogTrigger isOpen={isPickerOpen} onOpenChange={onOpenChange}>
        <AriaButton
          className={customButtonClassName}
          onPress={onCustomRangePress}
        >
          Custom range
        </AriaButton>
        <Popover className={popoverClassName}>
          <Dialog className="p-4">
            <RangeCalendar
              aria-label="Select billing period"
              value={displayRange ?? undefined}
              onChange={(range) => {
                if (range?.start && range?.end) {
                  onRangeComplete(range as DateRangeValue)
                } else {
                  onCalendarChange(range as DateRangeValue | null)
                }
              }}
              visibleDuration={{ months: 1 }}
              maxValue={maxValue ?? undefined}
              className="flex flex-col gap-3"
            >
              <header className="flex items-center justify-between gap-2">
                <AriaButton slot="previous" className={calendarNavButtonClassName}>
                  ‹
                </AriaButton>
                <Heading className="text-sm font-medium text-slate-100" />
                <AriaButton slot="next" className={calendarNavButtonClassName}>
                  ›
                </AriaButton>
              </header>
              <CalendarGrid className={calendarGridClassName}>
                {(date) => (
                  <CalendarCell
                    date={date}
                    className={calendarCellClassName}
                  />
                )}
              </CalendarGrid>
            </RangeCalendar>
          </Dialog>
        </Popover>
      </DialogTrigger>
    </div>
  )
}

export type { UsageRangeControlsProps }
