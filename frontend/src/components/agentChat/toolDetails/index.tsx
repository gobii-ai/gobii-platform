import type { ToolDetailComponent } from '../tooling/types'

import { GenericToolDetail, McpToolDetail, UpdateCharterDetail } from './details/common'
import { SqliteBatchDetail, EnableDatabaseDetail } from './details/sqlite'
import { SearchToolDetail } from './details/search'
import { ApiRequestDetail } from './details/api'
import { FileReadDetail, FileWriteDetail, FileExportDetail } from './details/files'
import { BrowserTaskDetail, BrightDataSnapshotDetail, BrightDataSearchDetail } from './details/browser'
import { LinkedInPersonProfileDetail, LinkedInCompanyProfileDetail, LinkedInPeopleSearchDetail, LinkedInJobListingsDetail, LinkedInPostsDetail } from './details/linkedin'
import { ReutersNewsDetail } from './details/news'
import { ZillowListingDetail } from './details/realestate'
import { RedditPostsDetail } from './details/reddit'
import { YahooFinanceBusinessDetail } from './details/finance'
import { CrunchbaseCompanyDetail } from './details/crunchbase'
import { AmazonProductDetail, AmazonProductReviewsDetail, AmazonProductSearchDetail } from './details/commerce'
import { RequestContactPermissionDetail, SecureCredentialsDetail } from './details/permissions'
import { AnalysisToolDetail } from './details/analysis'
import { UpdateScheduleDetail, AgentConfigUpdateDetail } from './details/schedule'
import { ChartDetail } from './details/chart'
import { KanbanUpdateDetail } from './details/kanban'

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
  BrightDataSearchDetail,
  AgentConfigUpdateDetail,
  LinkedInPersonProfileDetail,
  LinkedInCompanyProfileDetail,
  YahooFinanceBusinessDetail,
  CrunchbaseCompanyDetail,
  AmazonProductDetail,
  LinkedInPeopleSearchDetail,
  LinkedInJobListingsDetail,
  LinkedInPostsDetail,
  ReutersNewsDetail,
  ZillowListingDetail,
  RedditPostsDetail,
  AmazonProductReviewsDetail,
  AmazonProductSearchDetail,
  ChartDetail,
  KanbanUpdateDetail,
}

export const TOOL_DETAIL_COMPONENTS = {
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
  brightDataSearch: BrightDataSearchDetail,
  linkedinPerson: LinkedInPersonProfileDetail,
  linkedinCompany: LinkedInCompanyProfileDetail,
  yahooFinanceBusiness: YahooFinanceBusinessDetail,
  crunchbaseCompany: CrunchbaseCompanyDetail,
  amazonProduct: AmazonProductDetail,
  amazonProductReviews: AmazonProductReviewsDetail,
  amazonProductSearch: AmazonProductSearchDetail,
  linkedinPeopleSearch: LinkedInPeopleSearchDetail,
  linkedinJobListings: LinkedInJobListingsDetail,
  linkedinPosts: LinkedInPostsDetail,
  reutersNews: ReutersNewsDetail,
  zillowListing: ZillowListingDetail,
  redditPosts: RedditPostsDetail,
  chart: ChartDetail,
  mcpTool: McpToolDetail,
} satisfies Record<string, ToolDetailComponent>

export type DetailKind = keyof typeof TOOL_DETAIL_COMPONENTS

export function resolveDetailComponent(kind: DetailKind | null | undefined): ToolDetailComponent {
  if (!kind) return GenericToolDetail
  return TOOL_DETAIL_COMPONENTS[kind] ?? GenericToolDetail
}
