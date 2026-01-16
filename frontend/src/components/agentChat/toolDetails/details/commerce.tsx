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

function formatCount(value: number | null): string | null {
  if (value === null) return null
  return value.toLocaleString()
}

function shorten(value: string | null, max = 320): string | null {
  if (!value) return null
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

export function AmazonProductDetail({ entry }: ToolDetailProps) {
  const record = extractBrightDataFirstRecord(entry.result)

  if (!record) {
    return <p className="text-sm text-slate-500">No product details returned.</p>
  }

  const title = toText(record.title)
  const brand = toText(record.brand)
  const url = toText(record.url)
  const asin = toText(record.asin)
  const availability = toText(record.availability)
  const rating = toNumber(record.rating)
  const reviews = formatCount(toNumber(record.reviews_count))
  const sellerId = toText(record.seller_id)
  const sellerUrl = toText(record.seller_url)
  const seller = sellerUrl ? 'View seller' : sellerId
  const description = shorten(toText(record.description))
  const topReview = shorten(toText(record.top_review))
  const customerSays = shorten(toText(record.customer_says))
  const features = Array.isArray(record.features)
    ? (record.features as string[]).filter(isNonEmptyString).slice(0, 8)
    : []
  const imageUrl = toText(record.image_url) || toText(record.image)

  const infoItems = [
    title
      ? {
          label: 'Title',
          value: url ? (
            <a href={url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
              {title}
            </a>
          ) : (
            title
          ),
        }
      : null,
    brand ? { label: 'Brand', value: brand } : null,
    rating !== null ? { label: 'Rating', value: `${rating} / 5${reviews ? ` (${reviews} reviews)` : ''}` } : null,
    availability ? { label: 'Availability', value: availability } : null,
    reviews && rating === null ? { label: 'Reviews', value: reviews } : null,
    asin ? { label: 'ASIN', value: asin } : null,
    seller
      ? {
          label: 'Seller',
          value: sellerUrl ? (
            <a href={sellerUrl} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
              {seller}
            </a>
          ) : (
            seller
          ),
        }
      : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {imageUrl ? (
        <div className="overflow-hidden rounded-xl border border-slate-200/80 bg-white shadow-sm">
          <img src={imageUrl} alt={title ?? 'Product image'} className="w-full max-h-80 object-contain" />
        </div>
      ) : null}

      {description ? (
        <Section title="Description">
          <p className="leading-relaxed text-slate-700 whitespace-pre-wrap">{description}</p>
        </Section>
      ) : null}

      {features.length ? (
        <Section title="Key features">
          <ul className="list-disc space-y-1 pl-5 text-slate-700">
            {features.map((feature, idx) => (
              <li key={`${feature}-${idx}`}>{feature}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      {customerSays ? (
        <Section title="Customers say">
          <p className="leading-relaxed text-slate-700">{customerSays}</p>
        </Section>
      ) : null}

      {topReview ? (
        <Section title="Top review">
          <p className="leading-relaxed text-slate-700 whitespace-pre-wrap">{topReview}</p>
        </Section>
      ) : null}

      {!infoItems.some(Boolean) && !features.length && !description ? (
        <p className="text-slate-500">No product details returned.</p>
      ) : null}
    </div>
  )
}
