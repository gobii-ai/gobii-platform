import type { ToolDetailProps } from '../../tooling/types'
import { EmptyToolResult, ExternalLinkText, KeyValueList, ResultListSection, ToolResultCard } from '../shared'
import { extractBrightDataArray } from '../../../tooling/brightdata'
import { isNonEmptyString } from '../utils'
import { shorten, toNumber, toText } from '../brightDataUtils'

function formatMoney(value: number | null, currency = 'USD'): string | null {
  if (value === null) return null
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 }).format(value)
  } catch {
    return value.toLocaleString()
  }
}

function formatArea(value: number | null): string | null {
  if (value === null) return null
  return `${value.toLocaleString()} sqft`
}

type PropertyRecord = {
  title: string | null
  status: string | null
  url: string | null
  price: number | null
  zestimate: number | null
  rentZestimate: number | null
  beds: number | null
  baths: number | null
  sqft: number | null
  lotSqft: number | null
  yearBuilt: number | null
  zpid: number | null
  daysOnMarket: number | null
  description: string | null
}

function normalizeProperties(result: unknown): PropertyRecord[] {
  const records = extractBrightDataArray(result)
  return records
    .map((record) => {
      const addressRecord =
        record && typeof record === 'object' && 'address' in record && record.address && typeof record.address === 'object'
          ? (record.address as Record<string, unknown>)
          : null
      const addressParts = [
        toText(record.streetAddress) || toText(addressRecord?.streetAddress),
        toText(addressRecord?.city) || toText(record.city),
        toText(addressRecord?.state) || toText(record.state),
        toText(addressRecord?.zipcode) || toText(record.zipcode),
      ].filter(isNonEmptyString) as string[]

      return {
        title: addressParts.length ? addressParts.join(', ') : toText(record.url),
        status: toText(record.homeStatus),
        url: toText(record.url) || toText(record.hdpUrl),
        price: toNumber(record.price),
        zestimate: toNumber(record.zestimate),
        rentZestimate: toNumber(record.rentZestimate),
        beds: toNumber(record.bedrooms),
        baths: toNumber(record.bathrooms),
        sqft: toNumber(record.livingArea) ?? toNumber(record.livingAreaValue),
        lotSqft: toNumber(record.lotSize) ?? toNumber(record.lotAreaValue),
        yearBuilt: toNumber(record.yearBuilt),
        zpid: toNumber(record.zpid),
        daysOnMarket: toNumber(record.daysOnZillow) ?? toNumber(record.days_on_zillow),
        description: toText(record.description),
      }
    })
    .filter((item) => item.title || item.url)
}

export function ZillowListingDetail({ entry }: ToolDetailProps) {
  const properties = normalizeProperties(entry.result).slice(0, 6)

  if (!properties.length) {
    return <EmptyToolResult compact>No properties returned.</EmptyToolResult>
  }

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <ResultListSection title="Listings">
        {properties.map((property, idx) => {
          const price = formatMoney(property.price)
          const zestimate = formatMoney(property.zestimate)
          const rent = formatMoney(property.rentZestimate)
          const facts = [
            property.beds !== null ? `${property.beds} bd` : null,
            property.baths !== null ? `${property.baths} ba` : null,
            formatArea(property.sqft),
            property.yearBuilt !== null ? `Built ${property.yearBuilt}` : null,
            property.status ? property.status.replace(/_/g, ' ') : null,
          ].filter(Boolean)
          const lot = formatArea(property.lotSqft)

          const infoItems = [
            property.title
              ? {
                  label: 'Address',
                  value: property.url ? (
                    <ExternalLinkText href={property.url}>{property.title}</ExternalLinkText>
                  ) : (
                    property.title
                  ),
                }
              : null,
            price ? { label: 'Price', value: price } : null,
            zestimate ? { label: 'Zestimate', value: zestimate } : null,
            rent ? { label: 'Rent estimate', value: rent } : null,
            facts.length ? { label: 'Details', value: facts.join(' • ') } : null,
            lot ? { label: 'Lot', value: lot } : null,
            property.daysOnMarket !== null ? { label: 'Days on Zillow', value: property.daysOnMarket.toString() } : null,
            property.zpid !== null ? { label: 'ZPID', value: property.zpid.toString() } : null,
          ]

          const summary = shorten(property.description)

          return (
            <ToolResultCard key={`${property.url ?? property.title ?? idx}`}>
              <KeyValueList items={infoItems} />
              {summary ? <p className="mt-2 leading-relaxed text-slate-700 whitespace-pre-wrap">{summary}</p> : null}
            </ToolResultCard>
          )
        })}
      </ResultListSection>
    </div>
  )
}
