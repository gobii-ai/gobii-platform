import type { ToolDetailProps } from '../../tooling/types'
import { KeyValueList } from '../shared'
import { isNonEmptyString } from '../utils'
import { parseResultObject, isPlainObject } from '../../../../util/objectUtils'

type ProfileRecord = Record<string, unknown>

function pickProfile(result: unknown): ProfileRecord | null {
  const parsed = parseResultObject(result)

  const candidates: unknown[] = []
  if (Array.isArray(parsed)) {
    candidates.push(...parsed)
  } else if (isPlainObject(parsed)) {
    const asRecord = parsed as Record<string, unknown>
    if (Array.isArray(asRecord.result)) {
      candidates.push(...asRecord.result)
    } else {
      candidates.push(parsed)
    }
  }

  const firstObject = candidates.find((item) => isPlainObject(item)) as ProfileRecord | undefined
  return firstObject ?? null
}

function formatCount(value: unknown): string | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toLocaleString()
  }
  return null
}

const toText = (value: unknown): string | null => (isNonEmptyString(value) ? (value as string) : null)

export function LinkedInPersonProfileDetail({ entry }: ToolDetailProps) {
  const profile = pickProfile(entry.result)

  const name =
    toText(profile?.name) ||
    ([profile?.first_name, profile?.last_name].filter(isNonEmptyString).join(' ') || null)

  const currentCompany =
    profile?.current_company && isPlainObject(profile.current_company)
      ? (profile.current_company as Record<string, unknown>)
      : null
  const companyName = toText(currentCompany?.name) || toText(profile?.current_company_name)
  const companyLink = toText(currentCompany?.link)

  const followers = formatCount(profile?.followers)
  const connections = formatCount(profile?.connections)
  const city = isNonEmptyString(profile?.city) ? (profile?.city as string) : null
  const countryCode = isNonEmptyString(profile?.country_code) ? (profile?.country_code as string) : null
  const location = [city, countryCode].filter(Boolean).join(', ') || null
  const inputUrl =
    isPlainObject(profile?.input) && isNonEmptyString((profile?.input as Record<string, unknown>).url)
      ? ((profile?.input as Record<string, unknown>).url as string)
      : null
  const profileUrl = toText(profile?.url) || inputUrl
  const linkedinId = toText(profile?.linkedin_id) || toText(profile?.id)

  const infoItems = [
    name ? { label: 'Name', value: name } : null,
    companyName
      ? {
          label: 'Company',
          value: companyLink ? (
            <a href={companyLink as string} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
              {companyName}
            </a>
          ) : (
            companyName
          ),
        }
      : null,
    followers ? { label: 'Followers', value: followers } : null,
    connections ? { label: 'Connections', value: connections } : null,
    location ? { label: 'Location', value: location } : null,
    profileUrl
      ? {
          label: 'Profile',
          value: (
            <a href={profileUrl} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
              {profileUrl}
            </a>
          ),
        }
      : null,
    linkedinId ? { label: 'LinkedIn ID', value: linkedinId } : null,
  ]

  const hasDetails = infoItems.some(Boolean)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={infoItems} />
      {!hasDetails ? <p className="text-slate-500">No profile details returned.</p> : null}
    </div>
  )
}

function normalizeUrl(value: string | null): string | null {
  if (!value) return null
  if (/^https?:\/\//i.test(value)) return value
  return `https://${value}`
}

export function LinkedInCompanyProfileDetail({ entry }: ToolDetailProps) {
  const profile = pickProfile(entry.result)

  const name = toText(profile?.name)
  const websiteUrl = normalizeUrl(toText(profile?.website) || toText(profile?.website_simplified))
  const followers = formatCount(profile?.followers)
  const employees = formatCount(profile?.employees_in_linkedin)
  const companySize = toText(profile?.company_size)
  const orgType = toText(profile?.organization_type)
  const industry = toText(profile?.industries)
  const specialties = toText(profile?.specialties)
  const headquarters = toText(profile?.headquarters)
  const formattedLocations =
    Array.isArray(profile?.formatted_locations) && profile?.formatted_locations.length
      ? (profile?.formatted_locations as string[])
      : null
  const locations =
    Array.isArray(profile?.locations) && profile?.locations.length
      ? (profile?.locations as string[])
      : null
  const location = headquarters || formattedLocations?.find(isNonEmptyString) || locations?.find(isNonEmptyString) || null
  const profileUrl =
    toText(profile?.url) ||
    (isPlainObject(profile?.input) && isNonEmptyString((profile?.input as Record<string, unknown>).url)
      ? ((profile?.input as Record<string, unknown>).url as string)
      : null)
  const companyId = toText(profile?.company_id) || toText(profile?.id)
  const foundedYear = typeof profile?.founded === 'number' && Number.isFinite(profile.founded) ? profile.founded : null

  const infoItems = [
    name ? { label: 'Name', value: name } : null,
    websiteUrl
      ? {
          label: 'Website',
          value: (
            <a href={websiteUrl} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
              {websiteUrl}
            </a>
          ),
        }
      : null,
    followers ? { label: 'Followers', value: followers } : null,
    employees ? { label: 'Employees on LinkedIn', value: employees } : null,
    companySize ? { label: 'Company size', value: companySize } : null,
    orgType ? { label: 'Organization type', value: orgType } : null,
    industry ? { label: 'Industry', value: industry } : null,
    specialties ? { label: 'Specialties', value: specialties } : null,
    location ? { label: 'Headquarters', value: location } : null,
    foundedYear ? { label: 'Founded', value: String(foundedYear) } : null,
    profileUrl
      ? {
          label: 'LinkedIn',
          value: (
            <a href={profileUrl} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
              {profileUrl}
            </a>
          ),
        }
      : null,
    companyId ? { label: 'LinkedIn ID', value: companyId } : null,
  ]

  const hasDetails = infoItems.some(Boolean)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={infoItems} />
      {!hasDetails ? <p className="text-slate-500">No company details returned.</p> : null}
    </div>
  )
}
