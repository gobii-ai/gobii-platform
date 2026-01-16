import type { ToolDetailProps } from '../../tooling/types'
import { KeyValueList, Section } from '../shared'
import { extractBrightDataFirstRecord } from '../../../tooling/brightdata'
import { isNonEmptyString } from '../utils'

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const num = Number(value.replace(/[, ]+/g, ''))
    return Number.isFinite(num) ? num : null
  }
  return null
}

function formatNumber(value: number | null, fractionDigits = 2): string | null {
  if (value === null) return null
  return value.toLocaleString(undefined, { maximumFractionDigits: fractionDigits, minimumFractionDigits: 0 })
}

function formatCompact(value: number | null): string | null {
  if (value === null) return null
  return value.toLocaleString(undefined, { notation: 'compact', maximumFractionDigits: 1 })
}

type QuotePeer = { symbol: string; lastPrice: string | null; change: string | null }

function normalizePeers(raw: unknown, limit = 8): QuotePeer[] {
  if (!Array.isArray(raw)) return []
  return raw
    .slice(0, limit)
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const record = item as Record<string, unknown>
      const symbol = isNonEmptyString(record.symbol) ? record.symbol : null
      if (!symbol) return null
      const lastPrice = record.last_price ?? record.lastPrice
      const change = record.perc_change ?? record.percChange
      const lastPriceText = toNumber(lastPrice)
        ? formatNumber(toNumber(lastPrice))
        : isNonEmptyString(lastPrice)
          ? (lastPrice as string)
          : null
      const changeText = isNonEmptyString(change) ? (change as string) : null
      return { symbol, lastPrice: lastPriceText, change: changeText }
    })
    .filter((item): item is QuotePeer => Boolean(item))
}

export function YahooFinanceBusinessDetail({ entry }: ToolDetailProps) {
  const record = extractBrightDataFirstRecord(entry.result)

  const name = isNonEmptyString(record?.name) ? (record?.name as string) : null
  const ticker = isNonEmptyString(record?.stock_ticker) ? (record?.stock_ticker as string) : null
  const currency = isNonEmptyString(record?.currency) ? (record?.currency as string) : null
  const summary = isNonEmptyString(record?.summary) ? (record?.summary as string) : null
  const price = toNumber(record?.closing_price) ?? toNumber(record?.open) ?? toNumber(record?.previous_close)
  const prevClose = toNumber(record?.previous_close)
  const dayRange = isNonEmptyString(record?.day_range) ? (record?.day_range as string) : null
  const weekRange = isNonEmptyString(record?.week_range) ? (record?.week_range as string) : null
  const volume = toNumber(record?.volume)
  const avgVolume = toNumber(record?.avg_volume)
  const marketCap = toNumber(record?.market_cap)
  const beta = toNumber(record?.beta)
  const peRatio = toNumber(record?.pe_ratio)
  const eps = toNumber(record?.eps)
  const dividendYield = isNonEmptyString(record?.dividend_yield) ? (record?.dividend_yield as string) : null
  const earningsDate = isNonEmptyString(record?.earnings_date) ? (record?.earnings_date as string) : null
  const targetEst = toNumber(record?.target_est)
  const exchange = isNonEmptyString(record?.exchange) ? (record?.exchange as string) : null
  const url = isNonEmptyString(record?.url) ? (record?.url as string) : null
  const peers = normalizePeers(record?.people_also_watch)
  const analysts = record?.analyst_price_target && typeof record.analyst_price_target === 'object'
    ? (record.analyst_price_target as Record<string, unknown>)
    : null
  const analystTargets = {
    low: toNumber(analysts?.low),
    average: toNumber(analysts?.average),
    current: toNumber(analysts?.current),
    high: toNumber(analysts?.high),
  }

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
    ticker ? { label: 'Ticker', value: ticker } : null,
    summary ? { label: 'Summary', value: summary } : null,
    exchange ? { label: 'Exchange', value: exchange } : null,
    currency ? { label: 'Currency', value: currency } : null,
    price !== null
      ? {
          label: 'Price',
          value: `${formatNumber(price, 2)}${currency ? ` ${currency}` : ''}`,
        }
      : null,
    prevClose !== null ? { label: 'Previous close', value: formatNumber(prevClose, 2) } : null,
    dayRange ? { label: 'Day range', value: dayRange } : null,
    weekRange ? { label: '52-week range', value: weekRange } : null,
    marketCap !== null ? { label: 'Market cap', value: formatCompact(marketCap) ?? String(marketCap) } : null,
    volume !== null
      ? { label: 'Volume', value: `${formatNumber(volume, 0)}${avgVolume ? ` (avg ${formatNumber(avgVolume, 0)})` : ''}` }
      : null,
    peRatio !== null ? { label: 'P/E', value: formatNumber(peRatio, 2) } : null,
    eps !== null ? { label: 'EPS', value: formatNumber(eps, 2) } : null,
    beta !== null ? { label: 'Beta', value: formatNumber(beta, 2) } : null,
    dividendYield ? { label: 'Dividend yield', value: dividendYield } : null,
    earningsDate ? { label: 'Earnings date', value: earningsDate } : null,
    targetEst !== null ? { label: '1y target est.', value: formatNumber(targetEst, 2) } : null,
  ]

  return (
    <div className="space-y-4 text-sm text-slate-600">
      <KeyValueList items={infoItems} />

      {peers.length ? (
        <Section title="People also watch">
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {peers.map((peer) => (
              <li key={peer.symbol} className="rounded-lg border border-slate-200/80 px-3 py-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-slate-800">{peer.symbol}</span>
                  {peer.lastPrice ? <span className="text-slate-700">{peer.lastPrice}</span> : null}
                </div>
                {peer.change ? <p className="text-xs text-slate-500">{peer.change}</p> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {(analystTargets.low !== null || analystTargets.average !== null || analystTargets.high !== null) ? (
        <Section title="Analyst price targets">
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">Low</dt>
              <dd className="text-slate-800">{analystTargets.low !== null ? formatNumber(analystTargets.low, 2) : '—'}</dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">Average</dt>
              <dd className="text-slate-800">{analystTargets.average !== null ? formatNumber(analystTargets.average, 2) : '—'}</dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">High</dt>
              <dd className="text-slate-800">{analystTargets.high !== null ? formatNumber(analystTargets.high, 2) : '—'}</dd>
            </div>
          </dl>
        </Section>
      ) : null}

      {!infoItems.some(Boolean) && !peers.length ? <p className="text-slate-500">No finance details returned.</p> : null}
    </div>
  )
}
