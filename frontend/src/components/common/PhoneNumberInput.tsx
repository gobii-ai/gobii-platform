import { useCallback, useRef, type ChangeEvent } from 'react'

import {
  DEFAULT_PHONE_REGION,
  isSupportedPhoneRegion,
  SUPPORTED_PHONE_REGIONS,
} from './phoneCountries'

export { DEFAULT_PHONE_REGION } from './phoneCountries'

type PhoneNumberInputProps = {
  id?: string
  value: string
  region: string
  onValueChange: (value: string) => void
  onRegionChange: (region: string) => void
  disabled?: boolean
  inputClassName?: string
  selectClassName?: string
  className?: string
  placeholder?: string
}

function getDigitsBeforeCursor(value: string, cursor: number | null): number {
  if (cursor === null) {
    return value.replace(/\D/g, '').length
  }
  return value.slice(0, cursor).replace(/\D/g, '').length
}

function getCursorForDigitPosition(value: string, digitPosition: number): number {
  if (digitPosition <= 0) {
    return 0
  }
  let digitCount = 0
  for (let index = 0; index < value.length; index += 1) {
    if (/\d/.test(value[index])) {
      digitCount += 1
      if (digitCount >= digitPosition) {
        return index + 1
      }
    }
  }
  return value.length
}

export function normalizePhoneRegion(region: string): string {
  const normalized = region.toUpperCase()
  return isSupportedPhoneRegion(normalized) ? normalized : DEFAULT_PHONE_REGION
}

export function formatPhoneNational(number: string, region = DEFAULT_PHONE_REGION): string {
  const trimmed = number.trim()
  if (!trimmed || typeof window === 'undefined') {
    return number
  }
  const parser = window.libphonenumber?.parsePhoneNumber
  if (!parser) {
    return number
  }
  try {
    const parsed = parser(trimmed, normalizePhoneRegion(region))
    return parsed.formatNational?.() ?? number
  } catch {
    return number
  }
}

export function formatPhoneInputValue(value: string, region = DEFAULT_PHONE_REGION): string {
  if (!value || typeof window === 'undefined') {
    return value
  }
  const Formatter = window.libphonenumber?.AsYouType
  if (!Formatter) {
    return value
  }
  try {
    return new Formatter(normalizePhoneRegion(region)).input(value)
  } catch {
    return value
  }
}

export function formatPhoneE164(value: string, region = DEFAULT_PHONE_REGION): string {
  const trimmed = value.trim()
  if (!trimmed || typeof window === 'undefined') {
    return trimmed
  }
  const parser = window.libphonenumber?.parsePhoneNumber
  if (!parser) {
    return trimmed
  }
  try {
    return parser(trimmed, normalizePhoneRegion(region)).number ?? trimmed
  } catch {
    return trimmed
  }
}

export function PhoneNumberInput({
  id,
  value,
  region,
  onValueChange,
  onRegionChange,
  disabled = false,
  inputClassName,
  selectClassName,
  className,
  placeholder = '(123) 456-7890',
}: PhoneNumberInputProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const normalizedRegion = normalizePhoneRegion(region)

  const handleInputChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const raw = event.target.value
    const digitsBeforeCursor = getDigitsBeforeCursor(raw, event.target.selectionStart)
    const formatted = formatPhoneInputValue(raw, normalizedRegion)
    onValueChange(formatted)

    window.requestAnimationFrame(() => {
      const input = inputRef.current
      if (!input) {
        return
      }
      const nextCursor = getCursorForDigitPosition(formatted, digitsBeforeCursor)
      input.setSelectionRange(nextCursor, nextCursor)
    })
  }, [normalizedRegion, onValueChange])

  const handleRegionChange = useCallback((event: ChangeEvent<HTMLSelectElement>) => {
    const nextRegion = normalizePhoneRegion(event.target.value)
    onRegionChange(nextRegion)
    onValueChange(formatPhoneInputValue(value, nextRegion))
    window.requestAnimationFrame(() => {
      const input = inputRef.current
      input?.setSelectionRange(input.value.length, input.value.length)
    })
  }, [onRegionChange, onValueChange, value])

  return (
    <div className={`phone-number-input${className ? ` ${className}` : ''}`}>
      <select
        className={selectClassName}
        value={normalizedRegion}
        onChange={handleRegionChange}
        disabled={disabled}
        aria-label="Country code"
      >
        {SUPPORTED_PHONE_REGIONS.map((country) => (
          <option
            key={country.region}
            value={country.region}
            title={`${country.name} ${country.dialCode}`}
          >
            {country.region} {country.dialCode}
          </option>
        ))}
      </select>
      <input
        ref={inputRef}
        id={id}
        className={inputClassName}
        type="tel"
        inputMode="tel"
        autoComplete="tel-national"
        placeholder={placeholder}
        value={value}
        onChange={handleInputChange}
        disabled={disabled}
      />
    </div>
  )
}
