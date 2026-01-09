import type { ToolDetailComponent } from '../tooling/types'

import { GenericToolDetail, McpToolDetail, UpdateCharterDetail } from './details/common'
import { SqliteBatchDetail, EnableDatabaseDetail } from './details/sqlite'
import { SearchToolDetail } from './details/search'
import { ApiRequestDetail } from './details/api'
import { FileReadDetail, FileWriteDetail, FileExportDetail } from './details/files'
import { BrowserTaskDetail, BrightDataSnapshotDetail } from './details/browser'
import { RequestContactPermissionDetail, SecureCredentialsDetail } from './details/permissions'
import { AnalysisToolDetail } from './details/analysis'
import { UpdateScheduleDetail, AgentConfigUpdateDetail } from './details/schedule'

export { normalizeStructuredValue } from './normalize'
export {
  GenericToolDetail,
  UpdateCharterDetail,
  McpToolDetail,
  SqliteBatchDetail,
  EnableDatabaseDetail,
  SearchToolDetail,
  ApiRequestDetail,
  FileReadDetail,
  FileWriteDetail,
  FileExportDetail,
  BrowserTaskDetail,
  RequestContactPermissionDetail,
  SecureCredentialsDetail,
  AnalysisToolDetail,
  UpdateScheduleDetail,
  BrightDataSnapshotDetail,
  AgentConfigUpdateDetail,
}

export const TOOL_DETAIL_COMPONENTS: Record<string, ToolDetailComponent> = {
  default: GenericToolDetail,
  updateCharter: UpdateCharterDetail,
  sqliteBatch: SqliteBatchDetail,
  enableDatabase: EnableDatabaseDetail,
  search: SearchToolDetail,
  apiRequest: ApiRequestDetail,
  fileRead: FileReadDetail,
  fileWrite: FileWriteDetail,
  fileExport: FileExportDetail,
  browserTask: BrowserTaskDetail,
  contactPermission: RequestContactPermissionDetail,
  secureCredentials: SecureCredentialsDetail,
  analysis: AnalysisToolDetail,
  updateSchedule: UpdateScheduleDetail,
  brightDataSnapshot: BrightDataSnapshotDetail,
  mcpTool: McpToolDetail,
}

export function resolveDetailComponent(kind: string | null | undefined): ToolDetailComponent {
  if (!kind) return GenericToolDetail
  return TOOL_DETAIL_COMPONENTS[kind] ?? GenericToolDetail
}
