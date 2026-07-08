export type SupportedPhoneRegion = {
  region: string
  name: string
  dialCode: string
}

export const DEFAULT_PHONE_REGION = 'US'

export const SUPPORTED_PHONE_REGIONS: SupportedPhoneRegion[] = [
  { region: 'US', name: 'United States', dialCode: '+1' },
  { region: 'CA', name: 'Canada', dialCode: '+1' },
  { region: 'PR', name: 'Puerto Rico', dialCode: '+1' },
  { region: 'VG', name: 'British Virgin Islands', dialCode: '+1' },
  { region: 'VI', name: 'U.S. Virgin Islands', dialCode: '+1' },
  { region: 'IN', name: 'India', dialCode: '+91' },
  { region: 'JP', name: 'Japan', dialCode: '+81' },
  { region: 'AT', name: 'Austria', dialCode: '+43' },
  { region: 'BE', name: 'Belgium', dialCode: '+32' },
  { region: 'DK', name: 'Denmark', dialCode: '+45' },
  { region: 'FR', name: 'France', dialCode: '+33' },
  { region: 'DE', name: 'Germany', dialCode: '+49' },
  { region: 'IS', name: 'Iceland', dialCode: '+354' },
  { region: 'IE', name: 'Ireland', dialCode: '+353' },
  { region: 'IM', name: 'Isle of Man', dialCode: '+44' },
  { region: 'IT', name: 'Italy', dialCode: '+39' },
  { region: 'NL', name: 'Netherlands', dialCode: '+31' },
  { region: 'NO', name: 'Norway', dialCode: '+47' },
  { region: 'PT', name: 'Portugal', dialCode: '+351' },
  { region: 'ES', name: 'Spain', dialCode: '+34' },
  { region: 'SE', name: 'Sweden', dialCode: '+46' },
  { region: 'CH', name: 'Switzerland', dialCode: '+41' },
  { region: 'UA', name: 'Ukraine', dialCode: '+380' },
  { region: 'GB', name: 'United Kingdom', dialCode: '+44' },
  { region: 'AR', name: 'Argentina', dialCode: '+54' },
  { region: 'BR', name: 'Brazil', dialCode: '+55' },
  { region: 'CL', name: 'Chile', dialCode: '+56' },
  { region: 'EC', name: 'Ecuador', dialCode: '+593' },
  { region: 'PE', name: 'Peru', dialCode: '+51' },
  { region: 'AU', name: 'Australia', dialCode: '+61' },
  { region: 'CC', name: 'Cocos Islands', dialCode: '+61' },
  { region: 'CX', name: 'Christmas Island', dialCode: '+61' },
  { region: 'NZ', name: 'New Zealand', dialCode: '+64' },
]

export function isSupportedPhoneRegion(region: string): boolean {
  return SUPPORTED_PHONE_REGIONS.some((item) => item.region === region)
}
