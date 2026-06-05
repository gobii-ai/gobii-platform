import type { ToolDetailProps } from '../../tooling/types'
import { isRecord, parseResultObject } from '../../../../util/objectUtils'
import { createNormalizeContext, normalizeStructuredValue, tryParseJson } from '../normalize'
import { JsonBlock, KeyValueList, Section } from '../shared'
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

export function SqliteInternalTableDetail({ entry }: ToolDetailProps) {
  const sqliteInfo = entry.sqliteInfo
  const isAgentSkillsEntry = sqliteInfo?.kind === 'agentSkills'
  const isToolResultsQuery = sqliteInfo?.kind === 'toolResults' && sqliteInfo.operation === 'select'
  const stringResult = typeof entry.result === 'string' ? entry.result.trim() : null
  const parsedJsonResult = stringResult ? tryParseJson(stringResult) : null
  const objectResult =
    entry.result && typeof entry.result === 'object'
      ? (entry.result as Record<string, unknown> | unknown[])
      : null
  const structuredResult = objectResult ?? parsedJsonResult
  const normalizedStructuredResult =
    structuredResult !== null && structuredResult !== undefined
      ? normalizeStructuredValue(structuredResult, createNormalizeContext())
      : null
  const hasStructuredResult =
    Array.isArray(normalizedStructuredResult ?? structuredResult)
    || isRecord(normalizedStructuredResult ?? structuredResult)
  const instructionsText = typeof sqliteInfo?.instructionsText === 'string' && sqliteInfo.instructionsText.trim().length
    ? sqliteInfo.instructionsText
    : null
  const resultObject = parseResultObject(entry.result)
  const status =
    instructionsText
      ? null
      : entry.summary ??
    (typeof resultObject?.message === 'string' && resultObject.message.trim().length ? resultObject.message : null) ??
    (typeof resultObject?.status === 'string' && resultObject.status.trim().length ? resultObject.status : null)
  const fallbackResult =
    isToolResultsQuery
      ? (
        hasStructuredResult
          ? null
          : entry.result
            ? stringify(entry.result)
            : null
      )
      :
    status
      ? null
      : entry.result
        ? stringify(entry.result)
        : null

  if (isAgentSkillsEntry) {
    return (
      <div className="space-y-3 text-sm text-slate-600">
        {instructionsText ? (
          <Section title="Instructions">
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">
              {instructionsText}
            </pre>
          </Section>
        ) : null}
      </div>
    )
  }

  if (isToolResultsQuery) {
    return (
      <div className="space-y-3 text-sm text-slate-600">
        {hasStructuredResult ? (
          <Section title="Result">
            <JsonBlock value={(normalizedStructuredResult ?? structuredResult) as Record<string, unknown> | unknown[]} />
          </Section>
        ) : null}
        {fallbackResult ? (
          <Section title="Result">
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">
              {typeof fallbackResult === 'string' ? fallbackResult : stringify(fallbackResult)}
            </pre>
          </Section>
        ) : null}
      </div>
    )
  }

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {instructionsText ? (
        <Section title="Instructions">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">
            {instructionsText}
          </pre>
        </Section>
      ) : null}
      <KeyValueList
        items={[
          status ? { label: 'Status', value: status } : null,
          sqliteInfo ? { label: 'Table', value: sqliteInfo.tableName } : null,
          sqliteInfo ? { label: 'Operation', value: sqliteInfo.operationLabel } : null,
          entry.label ? { label: 'Action', value: entry.label } : null,
        ]}
      />
      {fallbackResult ? (
        <Section title="Result">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700 shadow-inner">
            {typeof fallbackResult === 'string' ? fallbackResult : stringify(fallbackResult)}
          </pre>
        </Section>
      ) : null}
    </div>
  )
}
