import { McpServersScreen } from '../McpServersScreen'

const MCP_PLACEHOLDER_ID = '00000000-0000-0000-0000-000000000000'

type ImmersiveMcpServersPageProps = {
  refreshKey?: number
  layout?: 'main' | 'sidebar-shell'
  pipedreamAppsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
}

export function ImmersiveMcpServersPage({
  refreshKey = 0,
  layout = 'main',
  pipedreamAppsUrl = null,
  pipedreamAppSearchUrl = null,
}: ImmersiveMcpServersPageProps) {
  return (
    <div className={layout === 'sidebar-shell' ? 'w-full px-1 pb-4' : 'mx-auto w-full max-w-5xl px-4 pb-6'}>
      <McpServersScreen
        key={refreshKey}
        listUrl="/console/api/mcp/servers/"
        detailUrlTemplate={`/console/api/mcp/servers/${MCP_PLACEHOLDER_ID}/`}
        assignmentUrlTemplate={`/console/api/mcp/servers/${MCP_PLACEHOLDER_ID}/assignments/`}
        testUrlTemplate={`/console/api/mcp/servers/${MCP_PLACEHOLDER_ID}/test/`}
        allowCommands={false}
        nativeIntegrationsUrl="/console/api/native-integrations/"
        pipedreamAppsUrl={pipedreamAppsUrl}
        pipedreamAppSearchUrl={pipedreamAppSearchUrl}
        oauthStartUrl="/console/api/mcp/oauth/start/"
        oauthMetadataUrl="/console/api/mcp/oauth/metadata/"
        oauthCallbackPath="/console/mcp/oauth/callback/"
        variant="embedded"
      />
    </div>
  )
}
