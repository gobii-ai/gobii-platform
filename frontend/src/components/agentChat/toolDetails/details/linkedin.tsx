import type { ToolDetailProps } from '../../tooling/types'
import { KeyValueList, Section } from '../shared'
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

function stripHtml(value: string | null): string | null {
  if (!value) return null
  return value.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
}

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

type PeopleResult = {
  name: string | null
  url: string | null
  location: string | null
  experience: string | null
  education: string | null
}

function normalizePeopleSearch(result: unknown): PeopleResult[] {
  const parsed = parseResultObject(result)
  const items = Array.isArray(parsed) ? parsed : isPlainObject(parsed) && Array.isArray((parsed as Record<string, unknown>).result)
    ? ((parsed as Record<string, unknown>).result as unknown[])
    : []

  return items
    .map((item) => {
      if (!isPlainObject(item)) return null
      const record = item as Record<string, unknown>
      return {
        name: toText(record.name),
        url: toText(record.url),
        location: toText(record.location),
        experience: toText(record.experience),
        education: toText(record.education),
      }
    })
    .filter((item): item is PeopleResult => Boolean(item && (item.name || item.url || item.location || item.experience || item.education)))
}

type JobResult = {
  title: string | null
  company: string | null
  location: string | null
  url: string | null
  summary: string | null
  applicants: number | null
  employmentType: string | null
  seniority: string | null
  salary: string | null
  posted: string | null
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[, ]+/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function normalizeJobListings(result: unknown): JobResult[] {
  const parsed = parseResultObject(result)
  const items = Array.isArray(parsed)
    ? parsed
    : isPlainObject(parsed) && Array.isArray((parsed as Record<string, unknown>).result)
      ? ((parsed as Record<string, unknown>).result as unknown[])
      : []

  return items
    .map((item) => {
      if (!isPlainObject(item)) return null
      const record = item as Record<string, unknown>
      const baseSalary =
        isPlainObject(record.base_salary) && record.base_salary !== null
          ? (record.base_salary as Record<string, unknown>)
          : null
      const salaryMin = toNumber(baseSalary?.min_amount)
      const salaryMax = toNumber(baseSalary?.max_amount)
      const salaryCurrency = toText(baseSalary?.currency)
      const salaryPeriod = toText(baseSalary?.payment_period)
      const salary =
        toText(record.job_base_pay_range) ||
        (salaryMin !== null && salaryMax !== null
          ? `${salaryMin.toLocaleString()} - ${salaryMax.toLocaleString()}${salaryCurrency ?? ''}${salaryPeriod ? `/${salaryPeriod}` : ''}`
          : null)
      const rawSummary =
        toText(record.job_summary) ||
        toText(record.description) ||
        toText(record.job_description) ||
        stripHtml(toText(record.job_description_formatted))
      return {
        title: toText(record.job_title) || toText(record.title),
        company: toText(record.company_name) || toText(record.company),
        location: toText(record.job_location) || toText(record.location),
        url: toText(record.apply_link) || toText(record.url),
        summary: rawSummary,
        applicants: toNumber(record.job_num_applicants ?? record.applicants),
        employmentType: toText(record.job_employment_type) || toText(record.employment_type),
        seniority: toText(record.job_seniority_level) || toText(record.seniority_level),
        salary,
        posted: toText(record.job_posted_time) || toText(record.job_posted_date),
      }
    })
    .filter((item): item is JobResult => Boolean(item && (item.title || item.company || item.url)))
}

function shorten(value: string | null, max = 360): string | null {
  if (!value) return null
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

export function LinkedInJobListingsDetail({ entry }: ToolDetailProps) {
  const jobs = normalizeJobListings(entry.result).slice(0, 10)

  if (!jobs.length) {
    return <p className="text-sm text-slate-500">No job listings returned.</p>
  }

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <Section title="Jobs">
        <div className="space-y-3">
          {jobs.map((job, idx) => {
            const metaParts = [
              job.company,
              job.location,
              job.employmentType,
              job.seniority,
            ].filter(Boolean)
            const statParts = [
              job.applicants !== null ? `${job.applicants.toLocaleString()} applicant${job.applicants === 1 ? '' : 's'}` : null,
              job.salary,
              job.posted,
            ].filter(Boolean)
            const summary = shorten(job.summary)

            return (
              <div key={`${job.title ?? job.company ?? 'job'}-${idx}`} className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 shadow-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-slate-900">
                    {job.url ? (
                      <a href={job.url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                        {job.title ?? 'Job listing'}
                      </a>
                    ) : (
                      job.title ?? 'Job listing'
                    )}
                  </span>
                </div>
                {metaParts.length ? (
                  <p className="text-xs text-slate-500">{metaParts.join(' • ')}</p>
                ) : null}
                {statParts.length ? (
                  <p className="text-xs text-slate-500">{statParts.join(' • ')}</p>
                ) : null}
                {summary ? <p className="mt-2 leading-relaxed text-slate-700">{summary}</p> : null}
              </div>
            )
          })}
        </div>
      </Section>
    </div>
  )
}

export function LinkedInPeopleSearchDetail({ entry }: ToolDetailProps) {
  const results = normalizePeopleSearch(entry.result).slice(0, 12)
  const hasResults = results.length > 0

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {hasResults ? (
        <Section title="People">
          <ul className="space-y-2">
            {results.map((item, idx) => (
              <li key={`${item.url ?? item.name ?? idx}`} className="rounded-lg border border-slate-200/80 px-3 py-2">
                <div className="flex flex-col gap-0.5">
                  <span className="font-semibold text-slate-800">
                    {item.url ? (
                      <a href={item.url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
                        {item.name ?? item.url}
                      </a>
                    ) : (
                      item.name ?? 'Unknown person'
                    )}
                  </span>
                  {item.location ? <span className="text-xs text-slate-500">{item.location}</span> : null}
                  {item.experience ? <span className="text-xs text-slate-600">{item.experience}</span> : null}
                  {item.education ? <span className="text-xs text-slate-600">{item.education}</span> : null}
                </div>
              </li>
            ))}
          </ul>
        </Section>
      ) : (
        <p className="text-slate-500">No people found.</p>
      )}
    </div>
  )
}
