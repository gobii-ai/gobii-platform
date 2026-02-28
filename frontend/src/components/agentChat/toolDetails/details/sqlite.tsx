import type { ToolDetailProps } from '../../tooling/types'
import { isRecord, parseResultObject } from '../../../../util/objectUtils'
import { KeyValueList, Section } from '../shared'
import { stringify } from '../utils'

export function SqliteBatchDetail({ entry }: ToolDetailProps) {
  const statements = (() => {
    if (entry.sqlStatements?.length) {
      return entry.sqlStatements
    }
    const params =
      entry.parameters && typeof entry.parameters === 'object'
        ? (entry.parameters as Record<string, unknown>)
        : null
    if (!params) {
      return null
    }
    const sqlParam = params['sql']
    if (typeof sqlParam === 'string') {
      return [sqlParam]
    }
    if (Array.isArray(sqlParam)) {
      return sqlParam.map(String)
    }
    const queryParam = params['query']
    if (typeof queryParam === 'string') {
      return [queryParam]
    }
    if (Array.isArray(queryParam)) {
      return queryParam.map(String)
    }
    const queriesParam = params['queries']
    if (typeof queriesParam === 'string') {
      return [queriesParam]
    }
    if (Array.isArray(queriesParam)) {
      return queriesParam.map(String)
    }
    if (Array.isArray(params['operations'])) {
      return params['operations'].map(String)
    }
    const resultObject = parseResultObject(entry.result)
    const translationValue = resultObject?.['translation']
    const translation =
      translationValue && typeof translationValue === 'object' && !Array.isArray(translationValue)
        ? (translationValue as Record<string, unknown>)
        : null
    const translatedQueries = translation?.['queries']
    if (Array.isArray(translatedQueries)) {
      return translatedQueries.map(String)
    }
    const generatedStatements = translation?.['generated_statements']
    if (Array.isArray(generatedStatements)) {
      return generatedStatements.map(String)
    }
    return null
  })()
  const result = entry.result
  return (
    <div className="space-y-3 text-sm text-slate-600">
      {statements && statements.length ? (
        <Section title={`SQL ${statements.length === 1 ? 'Statement' : 'Statements'}`}>
          <div className="space-y-2">
            {statements.map((statement, idx) => (
              <div key={idx} className="overflow-auto rounded-xl bg-slate-900/95 p-3 shadow-inner">
                <pre className="text-xs text-emerald-100">
                  <code className="language-sql">{statement}</code>
                </pre>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
      {result ? (
        <Section title="Result">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">{stringify(result)}</pre>
        </Section>
      ) : null}
    </div>
  )
}

export function EnableDatabaseDetail({ entry }: ToolDetailProps) {
  const resultObject = parseResultObject(entry.result)
  const statusValue = resultObject?.['status']
  const messageValue = resultObject?.['message']
  const managerValue = resultObject?.['tool_manager']
  const detailsValue = resultObject?.['details']

  const status = typeof statusValue === 'string' && statusValue.trim().length ? statusValue : null
  const message = typeof messageValue === 'string' && messageValue.trim().length ? messageValue : null
  const manager = isRecord(managerValue) ? managerValue : null
  const details = isRecord(detailsValue) ? detailsValue : null

  const toStringList = (value: unknown): string[] => {
    if (!Array.isArray(value)) return []
    return (value as unknown[])
      .map((item) => (typeof item === 'string' && item.trim().length > 0 ? item : null))
      .filter((item): item is string => Boolean(item))
  }

  const enabledList = toStringList(manager?.['enabled'])
  const alreadyEnabledList = toStringList(manager?.['already_enabled'])
  const evictedList = toStringList(manager?.['evicted'])
  const invalidList = toStringList(manager?.['invalid'])

  const renderedMessage = message ?? entry.summary ?? 'sqlite_batch availability updated.'

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <p className="text-slate-700">{renderedMessage}</p>
      <KeyValueList
        items={[
          status ? { label: 'Status', value: status.toUpperCase() } : null,
          enabledList.length ? { label: 'Enabled', value: enabledList.join(', ') } : null,
          alreadyEnabledList.length ? { label: 'Already enabled', value: alreadyEnabledList.join(', ') } : null,
          evictedList.length ? { label: 'Evicted', value: evictedList.join(', ') } : null,
          invalidList.length ? { label: 'Invalid', value: invalidList.join(', ') } : null,
        ]}
      />
      {details ? (
        <Section title="Details">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">
            {stringify(details)}
          </pre>
        </Section>
      ) : null}
    </div>
  )
}
