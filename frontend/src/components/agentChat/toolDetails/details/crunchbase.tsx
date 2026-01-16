import type { ToolDetailProps } from '../../tooling/types'
import { KeyValueList, Section } from '../shared'
import { extractBrightDataFirstRecord } from '../../../tooling/brightdata'
import { isNonEmptyString } from '../utils'

function toText(value: unknown): string | null {
  return isNonEmptyString(value) ? (value as string) : null
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[, ]+/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function formatCompact(value: number | null): string | null {
  if (value === null) return null
  return value.toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: 1 })
}

function formatMoney(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const formatted = toText(record.formatted_value)
  if (formatted) return formatted
  const numeric = toNumber(record.value)
  const currency = toText(record.currency)
  if (numeric !== null) {
    const display = formatCompact(numeric) ?? numeric.toString()
    return currency ? `${display} ${currency}` : display
  }
  return null
}

export function CrunchbaseCompanyDetail({ entry }: ToolDetailProps) {
  const record = extractBrightDataFirstRecord(entry.result)

  if (!record) {
    return <p className="text-sm text-slate-500">No company details returned.</p>
  }

  const name = toText(record.name)
  const url = toText(record.url)
  const cbRank = toNumber(record.cb_rank)
  const region = toText(record.region)
  const status = toText(record.operating_status)
  const companyType = toText(record.company_type)
  const industries = Array.isArray(record.industries)
    ? (record.industries as Array<Record<string, unknown>>)
        .map((item) => toText(item.value) ?? toText(item.id))
        .filter(isNonEmptyString)
    : null
  const fundingTotal = formatMoney(record.org_funding_total) ?? formatMoney(record.socfunding_total)
  const investors = toNumber(record.org_num_investors) ?? toNumber(record.num_investors)
  const employees = toNumber(record.num_employee_profiles)
  const contacts = toNumber(record.num_contacts ?? record.number_of_contacts)
  const traffic = toNumber(record.semrush_visits_latest_month ?? record.monthly_visits)
  const trafficDelta = toNumber(record.semrush_visits_mom_pct ?? record.monthly_visits_growth)
  const similar = Array.isArray(record.similar_companies)
    ? (record.similar_companies as Array<Record<string, unknown>>)
        .map((item) => ({
          name: toText(item.name),
          link: toText(item.link),
        }))
        .filter((item) => item.name)
        .slice(0, 8)
    : []

  const infoItems = [
    name
      ? {
          label: 'Name',
          value: url ? (
            <a href={url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
              {name}
            </a>
          ) : (
            name
          ),
        }
      : null,
    cbRank !== null ? { label: 'CB Rank', value: `#${cbRank}` } : null,
    region ? { label: 'Region', value: region } : null,
    status ? { label: 'Status', value: status } : null,
    companyType ? { label: 'Type', value: companyType } : null,
    industries?.length ? { label: 'Industries', value: industries.join(', ') } : null,
    fundingTotal ? { label: 'Total funding', value: fundingTotal } : null,
    investors !== null ? { label: 'Investors', value: investors.toLocaleString() } : null,
    employees !== null ? { label: 'Employee profiles', value: employees.toLocaleString() } : null,
    contacts !== null ? { label: 'Contacts', value: contacts.toLocaleString() } : null,
    traffic !== null
      ? {
          label: 'Monthly visits',
          value: `${formatCompact(traffic) ?? traffic.toString()}${trafficDelta !== null ? ` (${trafficDelta > 0 ? '+' : ''}${(trafficDelta * 100).toFixed(1)}%)` : ''}`,
        }
      : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {similar.length ? (
        <Section title="Similar companies">
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {similar.map((item) => (
              <li key={`${item.name}-${item.link ?? 'n/a'}`} className="rounded-lg border border-slate-200/80 px-3 py-2">
                {item.link ? (
                  <a href={item.link} target="_blank" rel="noreferrer" className="font-semibold text-indigo-600 underline">
                    {item.name}
                  </a>
                ) : (
                  <span className="font-semibold text-slate-800">{item.name}</span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {!infoItems.some(Boolean) && !similar.length ? <p className="text-slate-500">No company details returned.</p> : null}
    </div>
  )
}
