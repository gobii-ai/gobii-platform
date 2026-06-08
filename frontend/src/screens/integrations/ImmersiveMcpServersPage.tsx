import { ImmersivePageFrame } from '../../components/common/ImmersivePageFrame'
import { McpServersScreen } from '../McpServersScreen'

const MCP_PLACEHOLDER_ID = '00000000-0000-0000-0000-000000000000'

type ImmersiveMcpServersPageProps = {
  refreshKey?: number
  layout?: 'main' | 'sidebar-shell'
  nativeIntegrationsUrl?: string | null
  pipedreamAppsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
}

export function ImmersiveMcpServersPage({
  refreshKey = 0,
  layout = 'main',
  nativeIntegrationsUrl = '/console/api/native-integrations/',
  pipedreamAppsUrl = null,
  pipedreamAppSearchUrl = null,
}: ImmersiveMcpServersPageProps) {
  return (
    <ImmersivePageFrame layout={layout}>
      <McpServersScreen
        key={refreshKey}
        listUrl="/console/api/mcp/servers/"
        detailUrlTemplate={`/console/api/mcp/servers/${MCP_PLACEHOLDER_ID}/`}
        assignmentUrlTemplate={`/console/api/mcp/servers/${MCP_PLACEHOLDER_ID}/assignments/`}
        testUrlTemplate={`/console/api/mcp/servers/${MCP_PLACEHOLDER_ID}/test/`}
        allowCommands={false}
        nativeIntegrationsUrl={nativeIntegrationsUrl}
        pipedreamAppsUrl={pipedreamAppsUrl}
        pipedreamAppSearchUrl={pipedreamAppSearchUrl}
        oauthStartUrl="/console/api/mcp/oauth/start/"
        oauthMetadataUrl="/console/api/mcp/oauth/metadata/"
        oauthCallbackPath="/console/mcp/oauth/callback/"
        variant="embedded"
      />
    </ImmersivePageFrame>
  )
}
