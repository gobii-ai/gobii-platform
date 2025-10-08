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
  } = props

  return (
    <div className="flex flex-1 flex-wrap items-center gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <AriaButton
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 disabled:cursor-not-allowed disabled:border-slate-100 disabled:bg-slate-50 disabled:text-slate-300"
          isDisabled={!hasEffectiveRange}
          onPress={onPrevious}
        >
          ‹ Previous
        </AriaButton>
        <AriaButton
          className="rounded-md border border-transparent bg-blue-50 px-3 py-2 text-sm font-medium text-blue-600 transition-colors hover:bg-blue-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-300"
          isDisabled={!hasInitialRange || isCurrentSelection}
          onPress={onResetCurrent}
        >
          Current period
        </AriaButton>
        <AriaButton
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 disabled:cursor-not-allowed disabled:border-slate-100 disabled:bg-slate-50 disabled:text-slate-300"
          isDisabled={!hasEffectiveRange || isViewingCurrentBilling}
          onPress={onNext}
        >
          Next ›
        </AriaButton>
      </div>
      <div className="hidden h-10 w-px bg-slate-200 sm:block" aria-hidden="true" />
      <DialogTrigger isOpen={isPickerOpen} onOpenChange={onOpenChange}>
        <AriaButton
          className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:border-slate-300 hover:text-slate-700"
          onPress={onCustomRangePress}
        >
          Custom range
        </AriaButton>
        <Popover className="z-50 mt-2 rounded-xl border border-slate-200 bg-white shadow-xl">
          <Dialog className="p-4">
            <RangeCalendar
              aria-label="Select billing period"
              value={(calendarRange ?? effectiveRange) ?? undefined}
              onChange={(range) => {
                if (range?.start && range?.end) {
                  onRangeComplete(range as DateRangeValue)
                } else {
                  onCalendarChange(range as DateRangeValue | null)
                }
              }}
              visibleDuration={{ months: 1 }}
              className="flex flex-col gap-3"
            >
              <header className="flex items-center justify-between gap-2">
                <AriaButton slot="previous" className="rounded-md px-2 py-1 text-sm text-slate-600 transition-colors hover:bg-slate-100">
                  ‹
                </AriaButton>
                <Heading className="text-sm font-medium text-slate-700" />
                <AriaButton slot="next" className="rounded-md px-2 py-1 text-sm text-slate-600 transition-colors hover:bg-slate-100">
                  ›
                </AriaButton>
              </header>
              <CalendarGrid className="border-spacing-1 border-separate gap-y-1 text-center text-xs font-medium uppercase text-slate-500">
                {(date) => (
                  <CalendarCell
                    date={date}
                    className="m-0.5 flex h-8 w-8 items-center justify-center rounded-md text-sm text-slate-700 transition-colors hover:bg-blue-100 data-[disabled]:text-slate-300 data-[focused]:outline data-[focused]:outline-2 data-[focused]:outline-blue-400 data-[selected]:bg-blue-600 data-[selected]:text-white data-[range-selection]:bg-blue-100 data-[outside-month]:text-slate-300"
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
