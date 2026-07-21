import { Minus, Plus } from 'lucide-react'
import { Button, Group, Input, Label, NumberField } from 'react-aria-components'

type EvalRunCountFieldProps = {
  value: number
  onChange: (value: number) => void
  minValue?: number
  maxValue?: number
}

export function EvalRunCountField({
  value,
  onChange,
  minValue = 1,
  maxValue = 10,
}: EvalRunCountFieldProps) {
  return (
    <NumberField
      value={value}
      minValue={minValue}
      maxValue={maxValue}
      step={1}
      onChange={(nextValue) => {
        if (Number.isFinite(nextValue)) onChange(nextValue)
      }}
      className="flex flex-col gap-1"
    >
      <Label className="text-sm font-medium text-slate-700">Runs per scenario</Label>
      <Group className="flex items-center gap-2">
        <Button
          slot="decrement"
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-600 transition-colors hover:border-slate-400 hover:text-slate-900 focus:outline-none focus-visible:border-blue-500 focus-visible:ring-2 focus-visible:ring-blue-500/25 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Decrease runs per scenario"
        >
          <Minus className="h-4 w-4" strokeWidth={2.5} />
        </Button>
        <Input className="h-10 w-12 rounded-lg border border-slate-300 bg-white px-2 text-center text-sm font-semibold tabular-nums text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/25" />
        <Button
          slot="increment"
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-600 transition-colors hover:border-slate-400 hover:text-slate-900 focus:outline-none focus-visible:border-blue-500 focus-visible:ring-2 focus-visible:ring-blue-500/25 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Increase runs per scenario"
        >
          <Plus className="h-4 w-4" strokeWidth={2.5} />
        </Button>
      </Group>
    </NumberField>
  )
}
