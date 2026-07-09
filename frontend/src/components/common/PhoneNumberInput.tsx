import { useRef, type ChangeEvent } from 'react'

export const DEFAULT_PHONE_REGION = 'US'

export type SupportedPhoneRegion = { region: string; name: string; dialCode: string }

function getSupportedPhoneRegion(region: string, supportedRegions: SupportedPhoneRegion[]): SupportedPhoneRegion {
  return supportedRegions.find((item) => item.region === region) ?? supportedRegions[0] ?? {
    region: DEFAULT_PHONE_REGION,
    name: 'United States',
    dialCode: '+1',
  }
}

type PhoneNumberInputProps = {
  id?: string
  value: string
  region: string
  onValueChange: (value: string) => void
  onRegionChange: (region: string) => void
  supportedRegions: SupportedPhoneRegion[]
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

function getDigits(value: string): string {
  return value.replace(/\D/g, '')
}

function isNanpRegion(region: string, supportedRegions: SupportedPhoneRegion[]): boolean {
  return getSupportedPhoneRegion(region, supportedRegions).dialCode === '+1'
}

function stripNanpCountryCode(digits: string): string {
  return digits.length > 10 && digits.startsWith('1') ? digits.slice(1) : digits
}

function formatNanpNational(value: string): string {
  const digits = stripNanpCountryCode(getDigits(value)).slice(0, 10)
  if (digits.length <= 3) {
    return digits
  }
  if (digits.length <= 6) {
    return `(${digits.slice(0, 3)}) ${digits.slice(3)}`
  }
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`
}

function formatPhoneInputFallback(value: string, region: string, supportedRegions: SupportedPhoneRegion[]): string {
  if (isNanpRegion(region, supportedRegions)) {
    return formatNanpNational(value)
  }
  return value
}

function formatPhoneE164Fallback(value: string, region: string, supportedRegions: SupportedPhoneRegion[]): string {
  const trimmed = value.trim()
  const digits = getDigits(trimmed)
  if (!digits) {
    return trimmed
  }
  if (trimmed.startsWith('+')) {
    return `+${digits}`
  }
  const country = getSupportedPhoneRegion(region, supportedRegions)
  const dialDigits = getDigits(country.dialCode)
  if (isNanpRegion(region, supportedRegions)) {
    const nationalDigits = stripNanpCountryCode(digits)
    return nationalDigits.length === 10 ? `${country.dialCode}${nationalDigits}` : trimmed
  }
  if (digits.startsWith(dialDigits) && digits.length > dialDigits.length) {
    return `+${digits}`
  }
  return `${country.dialCode}${digits}`
}

export function normalizePhoneRegion(region: string, supportedRegions: SupportedPhoneRegion[]): string {
  const normalized = region.toUpperCase()
  return supportedRegions.some((item) => item.region === normalized)
    ? normalized
    : getSupportedPhoneRegion(DEFAULT_PHONE_REGION, supportedRegions).region
}

export function formatPhoneNational(
  number: string,
  region = DEFAULT_PHONE_REGION,
  supportedRegions: SupportedPhoneRegion[] = [],
): string {
  const trimmed = number.trim()
  if (!trimmed || typeof window === 'undefined') {
    return number
  }
  const parser = window.libphonenumber?.parsePhoneNumber
  if (!parser) {
    return formatPhoneInputFallback(number, region, supportedRegions)
  }
  try {
    const parsed = parser(trimmed, normalizePhoneRegion(region, supportedRegions))
    return parsed.formatNational?.() ?? formatPhoneInputFallback(number, region, supportedRegions)
  } catch {
    return formatPhoneInputFallback(number, region, supportedRegions)
  }
}

export function formatPhoneInputValue(
  value: string,
  region = DEFAULT_PHONE_REGION,
  supportedRegions: SupportedPhoneRegion[] = [],
): string {
  if (!value) {
    return value
  }
  if (typeof window === 'undefined') {
    return formatPhoneInputFallback(value, region, supportedRegions)
  }
  const Formatter = window.libphonenumber?.AsYouType
  if (!Formatter) {
    return formatPhoneInputFallback(value, region, supportedRegions)
  }
  try {
    return new Formatter(normalizePhoneRegion(region, supportedRegions)).input(value)
  } catch {
    return formatPhoneInputFallback(value, region, supportedRegions)
  }
}

export function formatPhoneE164(
  value: string,
  region = DEFAULT_PHONE_REGION,
  supportedRegions: SupportedPhoneRegion[] = [],
): string {
  const trimmed = value.trim()
  if (!trimmed) {
    return trimmed
  }
  if (typeof window === 'undefined') {
    return formatPhoneE164Fallback(trimmed, region, supportedRegions)
  }
  const parser = window.libphonenumber?.parsePhoneNumber
  if (!parser) {
    return formatPhoneE164Fallback(trimmed, region, supportedRegions)
  }
  try {
    return parser(trimmed, normalizePhoneRegion(region, supportedRegions)).number
      ?? formatPhoneE164Fallback(trimmed, region, supportedRegions)
  } catch {
    return formatPhoneE164Fallback(trimmed, region, supportedRegions)
  }
}

export function PhoneNumberInput({
  id,
  value,
  region,
  onValueChange,
  onRegionChange,
  supportedRegions,
  disabled = false,
  inputClassName,
  selectClassName,
  className,
  placeholder = '(123) 456-7890',
}: PhoneNumberInputProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const availableRegions = supportedRegions.length ? supportedRegions : [getSupportedPhoneRegion(DEFAULT_PHONE_REGION, supportedRegions)]
  const normalizedRegion = normalizePhoneRegion(region, availableRegions)

  function handleInputChange(event: ChangeEvent<HTMLInputElement>) {
    const raw = event.target.value
    const digitsBeforeCursor = getDigitsBeforeCursor(raw, event.target.selectionStart)
    const formatted = formatPhoneInputValue(raw, normalizedRegion, availableRegions)
    onValueChange(formatted)

    window.requestAnimationFrame(() => {
      const input = inputRef.current
      if (!input) {
        return
      }
      const nextCursor = getCursorForDigitPosition(formatted, digitsBeforeCursor)
      input.setSelectionRange(nextCursor, nextCursor)
    })
  }

  function handleRegionChange(event: ChangeEvent<HTMLSelectElement>) {
    const nextRegion = normalizePhoneRegion(event.target.value, availableRegions)
    onRegionChange(nextRegion)
    onValueChange(formatPhoneInputValue(value, nextRegion, availableRegions))
    window.requestAnimationFrame(() => {
      const input = inputRef.current
      input?.setSelectionRange(input.value.length, input.value.length)
    })
  }

  return (
    <div className={`phone-number-input${className ? ` ${className}` : ''}`}>
      <select
        className={selectClassName}
        value={normalizedRegion}
        onChange={handleRegionChange}
        disabled={disabled}
        aria-label="Country code"
      >
        {availableRegions.map((country) => (
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
