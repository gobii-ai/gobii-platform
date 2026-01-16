import type { ToolDetailProps } from '../../tooling/types'
import { KeyValueList, Section } from '../shared'
import { extractBrightDataArray, extractBrightDataFirstRecord } from '../../../tooling/brightdata'
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

function formatRatingValue(value: number | null): string | null {
  if (value === null) return null
  return Number.isInteger(value) ? value.toString() : value.toFixed(1)
}

type RatingBreakdown = {
  stars: number
  count: number
  percent: number
  countLabel: string | null
}

function buildRatingBreakdown(ratingObject: unknown, ratingTotal: number | null, ratingMax: number): RatingBreakdown[] {
  if (!ratingObject || typeof ratingObject !== 'object') return []
  const record = ratingObject as Record<string, unknown>
  const starKeys: Array<{ key: string; stars: number }> = [
    { key: 'five_star', stars: 5 },
    { key: 'four_star', stars: 4 },
    { key: 'three_star', stars: 3 },
    { key: 'two_star', stars: 2 },
    { key: 'one_star', stars: 1 },
  ]
  const boundedMax = Number.isFinite(ratingMax) && ratingMax > 0 ? ratingMax : 5

  const entries = starKeys
    .filter(({ stars }) => stars <= boundedMax)
    .map(({ key, stars }) => ({
      stars,
      count: toNumber(record[key]) ?? 0,
    }))

  const sumCounts = entries.reduce((sum, item) => sum + item.count, 0)
  const total = ratingTotal ?? (sumCounts > 0 ? sumCounts : null)
  if (!total || total <= 0) return entries.filter((item) => item.count > 0).map((item) => ({
    ...item,
    percent: 0,
    countLabel: formatCount(item.count),
  }))

  return entries.map((item) => ({
    ...item,
    percent: Math.min(100, Math.max(0, (item.count / total) * 100)),
    countLabel: formatCount(item.count),
  }))
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

export function AmazonProductReviewsDetail({ entry }: ToolDetailProps) {
  const records = extractBrightDataArray(entry.result)
  const reviews = records.slice(0, 10)
  const product = reviews[0] ?? null

  const inputCandidate = product && typeof product === 'object' ? (product as Record<string, unknown>).input : null
  const inputUrl =
    inputCandidate && typeof inputCandidate === 'object' && !Array.isArray(inputCandidate)
      ? toText((inputCandidate as Record<string, unknown>).url)
      : null
  const productName = toText(product?.product_name)
  const productUrl = toText(product?.url) || toText(product?.product_url) || inputUrl
  const productRating = toNumber(product?.product_rating)
  const ratingMax = toNumber(product?.product_rating_max) ?? 5
  const productRatingCountValue =
    toNumber(product?.product_rating_count) ??
    toNumber(product?.rating_count) ??
    toNumber(product?.reviews_count) ??
    null
  const productRatingCount = formatCount(productRatingCountValue)
  const asin = toText(product?.asin) || toText(product?.product_asin)
  const ratingBreakdown = buildRatingBreakdown(product?.product_rating_object, productRatingCountValue, ratingMax).filter(
    (item) => item.count > 0,
  )

  const ratingText = formatRatingValue(productRating)
  const ratingSummary =
    ratingText !== null
      ? `${ratingText} / ${ratingMax}${productRatingCount ? ` (${productRatingCount} ratings)` : ''}`
      : productRatingCount
        ? `${productRatingCount} ratings`
        : null

  const infoItems = [
    productName
      ? {
          label: 'Product',
          value: productUrl ? (
            <a href={productUrl} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
              {productName}
            </a>
          ) : (
            productName
          ),
        }
      : null,
    ratingSummary ? { label: 'Rating', value: ratingSummary } : null,
    asin ? { label: 'ASIN', value: asin } : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {ratingBreakdown.length ? (
        <Section title="Rating breakdown">
          <div className="space-y-2">
            {ratingBreakdown.map((item) => {
              const width = item.percent > 0 ? Math.max(2, item.percent) : item.count > 0 ? 2 : 0
              return (
                <div key={item.stars} className="flex items-center gap-3">
                  <span className="w-12 text-xs font-semibold text-slate-700">{item.stars}-star</span>
                  <div className="h-2 flex-1 rounded-full bg-slate-100">
                    <div className="h-full rounded-full bg-amber-500" style={{ width: `${width}%` }} />
                  </div>
                  <span className="w-16 text-right text-xs text-slate-600">{item.countLabel ?? '0'}</span>
                </div>
              )
            })}
          </div>
        </Section>
      ) : null}

      {reviews.length ? (
        <Section title="Reviews">
          <div className="space-y-3">
            {reviews.map((review, idx) => {
              const header = toText(review.review_header) || toText(review.title) || 'Review'
              const rating = toNumber(review.rating)
              const ratingLabel = formatRatingValue(rating)
              const ratingStars = rating !== null ? '★'.repeat(Math.max(1, Math.min(5, Math.round(rating)))) : null
              const author = toText(review.author_name) || toText(review.author)
              const posted = toText(review.review_posted_date) || toText(review.date)
              const country = toText(review.review_country)
              const text = shorten(toText(review.review_text) || toText(review.text), 640)
              const helpfulCount = formatCount(toNumber(review.helpful_count))
              const badge = toText(review.badge)
              const verifiedText = review.is_verified && (!badge || !badge.toLowerCase().includes('verified')) ? 'Verified purchase' : null
              const vineText = review.is_amazon_vine ? 'Vine review' : null
              const metaParts = [
                author,
                posted,
                country,
                badge,
                verifiedText,
                vineText,
                helpfulCount ? `${helpfulCount} helpful` : null,
              ].filter(Boolean)

              return (
                <div key={`${header}-${idx}`} className="rounded-lg border border-slate-200/70 bg-white px-3 py-2 shadow-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-slate-800">{header}</span>
                    {ratingLabel ? (
                      <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">
                        {ratingStars ? <span className="mr-1 tracking-tight">{ratingStars}</span> : null}
                        {`${ratingLabel} / ${ratingMax}`}
                      </span>
                    ) : null}
                  </div>
                  {metaParts.length ? (
                    <p className="text-xs text-slate-500">{metaParts.join(' • ')}</p>
                  ) : null}
                  {text ? <p className="mt-2 leading-relaxed whitespace-pre-wrap text-slate-700">{text}</p> : null}
                </div>
              )
            })}
          </div>
        </Section>
      ) : (
        <p className="text-slate-500">No reviews returned.</p>
      )}
    </div>
  )
}
