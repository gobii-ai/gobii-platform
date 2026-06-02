import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  ConnectionMode,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import ELK from 'elkjs/lib/elk.bundled.js'
import { Building2, Link2, RefreshCw, Save, Trash2, UserRoundCheck } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  fetchAgentOrgChart,
  saveAgentOrgChart,
  type AgentOrgChartPayload,
} from '../../api/agents'
import { HttpError } from '../../api/http'
import type { AgentRosterEntry } from '../../types/agentRoster'
import { AgentAvatarBadge } from '../common/AgentAvatarBadge'

type AgentOrgChartViewProps = {
  agents: AgentRosterEntry[]
  activeAgentId?: string | null
  switchingAgentId?: string | null
  onSelectAgent?: (agent: AgentRosterEntry) => void
}

type AgentOrgChartNodeData = {
  agent: AgentRosterEntry
  active: boolean
  switching: boolean
  onSelectAgent?: (agent: AgentRosterEntry) => void
}

const NODE_WIDTH = 136
const NODE_HEIGHT = 116
const LAYOUT_NODE_WIDTH = 158
const LAYOUT_NODE_HEIGHT = 158
const COMPONENT_GAP_X = 56
const COMPONENT_GAP_Y = 72
const COMPONENT_ROW_WIDTH = 760
const elk = new ELK()

function buildInitialPosition(index: number): { x: number; y: number } {
  const column = index % 3
  const row = Math.floor(index / 3)
  return {
    x: column * 320,
    y: row * 240,
  }
}

function AgentOrgNode({ data }: NodeProps<Node<AgentOrgChartNodeData>>) {
  const { agent, active, switching, onSelectAgent } = data
  const accentStyle = agent.displayColorHex
    ? ({ '--agent-org-accent': agent.displayColorHex } as CSSProperties)
    : undefined
  const miniDescription = (agent.miniDescription || agent.shortDescription || '').trim()

  return (
    <div
      className="agent-org-chart-node"
      data-active={active ? 'true' : 'false'}
      data-switching={switching ? 'true' : 'false'}
      style={accentStyle}
    >
      <span
        className="agent-org-chart-node-link-control agent-org-chart-node-link-control--target"
        aria-hidden="true"
      >
        <UserRoundCheck className="h-4 w-4" />
      </span>
      <Handle
        type="target"
        position={Position.Top}
        className="agent-org-chart-node-handle agent-org-chart-node-handle--target"
        title="Drop manager connection here"
      />
      <button
        type="button"
        className="agent-org-chart-node-main"
        onClick={() => onSelectAgent?.(agent)}
      >
        <AgentAvatarBadge
          name={agent.name || 'Agent'}
          avatarUrl={agent.avatarUrl}
          className="agent-org-chart-node-avatar"
          imageClassName="agent-org-chart-node-avatar-image"
          textClassName="agent-org-chart-node-avatar-text"
        />
        <span className="agent-org-chart-node-copy">
          <span className="agent-org-chart-node-name">{agent.name || 'Agent'}</span>
          {miniDescription ? (
            <span className="agent-org-chart-node-desc">{miniDescription}</span>
          ) : null}
        </span>
      </button>
      <Handle
        type="source"
        position={Position.Bottom}
        className="agent-org-chart-node-handle agent-org-chart-node-handle--source"
        title="Drag to connect a child agent"
      >
        <Link2 className="h-4 w-4" />
      </Handle>
    </div>
  )
}

const nodeTypes = {
  agentOrgNode: AgentOrgNode,
}

function buildFlowEdges(chart: AgentOrgChartPayload | null): Edge[] {
  return (chart?.edges ?? []).map((edge) => ({
    id: edge.id ?? `${edge.parentAgentId}:${edge.childAgentId}`,
    source: edge.parentAgentId,
    target: edge.childAgentId,
    type: 'smoothstep',
    markerEnd: { type: MarkerType.ArrowClosed },
    data: {
      peerLinkId: edge.peerLinkId,
    },
  }))
}

function buildFlowNodes(
  agents: AgentRosterEntry[],
  chart: AgentOrgChartPayload | null,
  activeAgentId: string | null | undefined,
  switchingAgentId: string | null | undefined,
  onSelectAgent: ((agent: AgentRosterEntry) => void) | undefined,
): Node<AgentOrgChartNodeData>[] {
  const savedPositions = new Map((chart?.nodes ?? []).map((node) => [node.agentId, node]))
  return agents.map((agent, index) => {
    const saved = savedPositions.get(agent.id)
    const fallback = buildInitialPosition(index)
    return {
      id: agent.id,
      type: 'agentOrgNode',
      position: {
        x: typeof saved?.x === 'number' ? saved.x : fallback.x,
        y: typeof saved?.y === 'number' ? saved.y : fallback.y,
      },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      data: {
        agent,
        active: agent.id === activeAgentId,
        switching: agent.id === switchingAgentId,
        onSelectAgent,
      },
    }
  })
}

function connectionCreatesCycle(edges: Edge[], source: string, target: string): boolean {
  const childrenByParent = new Map<string, string[]>()
  edges.forEach((edge) => {
    const children = childrenByParent.get(edge.source) ?? []
    children.push(edge.target)
    childrenByParent.set(edge.source, children)
  })

  const visited = new Set<string>()
  const queue = [target]

  while (queue.length > 0) {
    const current = queue.shift()
    if (!current || visited.has(current)) {
      continue
    }
    if (current === source) {
      return true
    }
    visited.add(current)
    queue.push(...(childrenByParent.get(current) ?? []))
  }

  return false
}

function packLayoutComponents(
  nodes: Node<AgentOrgChartNodeData>[],
  edges: Edge[],
): Node<AgentOrgChartNodeData>[] {
  const nodesById = new Map(nodes.map((node) => [node.id, node]))
  const neighborsByNode = new Map(nodes.map((node) => [node.id, new Set<string>()]))

  edges.forEach((edge) => {
    neighborsByNode.get(edge.source)?.add(edge.target)
    neighborsByNode.get(edge.target)?.add(edge.source)
  })

  const visited = new Set<string>()
  const components: Array<{
    ids: string[]
    minX: number
    minY: number
    width: number
    height: number
  }> = []

  nodes.forEach((node) => {
    if (visited.has(node.id)) {
      return
    }

    const ids: string[] = []
    const queue = [node.id]
    visited.add(node.id)

    while (queue.length > 0) {
      const currentId = queue.shift()
      if (!currentId) {
        continue
      }
      ids.push(currentId)
      neighborsByNode.get(currentId)?.forEach((neighborId) => {
        if (!visited.has(neighborId)) {
          visited.add(neighborId)
          queue.push(neighborId)
        }
      })
    }

    const componentNodes = ids
      .map((id) => nodesById.get(id))
      .filter((componentNode): componentNode is Node<AgentOrgChartNodeData> => Boolean(componentNode))
    const minX = Math.min(...componentNodes.map((componentNode) => componentNode.position.x))
    const minY = Math.min(...componentNodes.map((componentNode) => componentNode.position.y))
    const maxX = Math.max(...componentNodes.map((componentNode) => componentNode.position.x + LAYOUT_NODE_WIDTH))
    const maxY = Math.max(...componentNodes.map((componentNode) => componentNode.position.y + LAYOUT_NODE_HEIGHT))

    components.push({
      ids,
      minX,
      minY,
      width: maxX - minX,
      height: maxY - minY,
    })
  })

  components.sort((a, b) => b.ids.length - a.ids.length || a.minY - b.minY || a.minX - b.minX)

  const offsets = new Map<string, { x: number; y: number }>()
  let cursorX = 0
  let cursorY = 0
  let rowHeight = 0

  components.forEach((component) => {
    if (cursorX > 0 && cursorX + component.width > COMPONENT_ROW_WIDTH) {
      cursorX = 0
      cursorY += rowHeight + COMPONENT_GAP_Y
      rowHeight = 0
    }

    component.ids.forEach((id) => {
      offsets.set(id, {
        x: cursorX - component.minX,
        y: cursorY - component.minY,
      })
    })

    cursorX += component.width + COMPONENT_GAP_X
    rowHeight = Math.max(rowHeight, component.height)
  })

  return nodes.map((node) => {
    const offset = offsets.get(node.id)
    if (!offset) {
      return node
    }
    return {
      ...node,
      position: {
        x: node.position.x + offset.x,
        y: node.position.y + offset.y,
      },
    }
  })
}

async function layoutNodes(nodes: Node<AgentOrgChartNodeData>[], edges: Edge[]) {
  const graph = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'DOWN',
      'elk.spacing.nodeNode': '44',
      'elk.layered.spacing.nodeNodeBetweenLayers': '72',
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: LAYOUT_NODE_WIDTH,
      height: LAYOUT_NODE_HEIGHT,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  }
  const layout = await elk.layout(graph)
  const positions = new Map((layout.children ?? []).map((child) => [child.id, child]))
  const layoutedNodes = nodes.map((node) => {
    const position = positions.get(node.id)
    if (!position) {
      return node
    }
    return {
      ...node,
      position: {
        x: position.x ?? node.position.x,
        y: position.y ?? node.position.y,
      },
    }
  })
  return packLayoutComponents(layoutedNodes, edges)
}

function getErrorMessage(error: unknown): string {
  if (error instanceof HttpError) {
    if (error.status === 409) {
      return 'Org chart changed elsewhere. Reload and try again.'
    }
    if (error.status === 403) {
      return 'You do not have permission to update this org chart.'
    }
    if (typeof error.body === 'string') {
      return error.body
    }
    if (error.body && typeof error.body === 'object' && 'error' in error.body) {
      const message = (error.body as { error?: unknown }).error
      if (typeof message === 'string') {
        return message
      }
    }
  }
  return 'Unable to save org chart.'
}

function AgentOrgChartCanvas({
  agents,
  activeAgentId,
  switchingAgentId,
  onSelectAgent,
}: AgentOrgChartViewProps) {
  const queryClient = useQueryClient()
  const chartQueryKey = useMemo(
    () => ['agent-org-chart', agents.map((agent) => agent.id).sort().join(':')] as const,
    [agents],
  )
  const chartQuery = useQuery({
    queryKey: chartQueryKey,
    queryFn: fetchAgentOrgChart,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  })
  const chart = chartQuery.data ?? null
  const [nodes, setNodes] = useState<Node<AgentOrgChartNodeData>[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance<Node<AgentOrgChartNodeData>, Edge> | null>(null)
  const nodeIdentity = useMemo(() => nodes.map((node) => node.id).sort().join(':'), [nodes])

  const fitOrgChartView = useCallback((duration = 0) => {
    void reactFlowInstance?.fitView({
      padding: 0.24,
      minZoom: 0.82,
      maxZoom: 1.18,
      duration,
    })
  }, [reactFlowInstance])

  useEffect(() => {
    const nextNodes = buildFlowNodes(
      agents,
      chart,
      activeAgentId,
      switchingAgentId,
      onSelectAgent,
    )
    const nextEdges = buildFlowEdges(chart)
    const hasMissingPositions = (chart?.nodes ?? []).some((node) => node.x === null || node.y === null)
    if (hasMissingPositions && nextNodes.length > 0) {
      void layoutNodes(nextNodes, nextEdges).then((layoutedNodes) => {
        setNodes(layoutedNodes)
        setEdges(nextEdges)
        setDirty(true)
      })
      return
    }
    setNodes(nextNodes)
    setEdges(nextEdges)
    setDirty(false)
  }, [activeAgentId, agents, chart, onSelectAgent, switchingAgentId])

  useEffect(() => {
    if (!reactFlowInstance || !nodeIdentity) {
      return
    }

    const animationFrame = window.requestAnimationFrame(() => fitOrgChartView(0))
    const shortDelay = window.setTimeout(() => fitOrgChartView(120), 180)
    const galleryAnimationDelay = window.setTimeout(() => fitOrgChartView(180), 420)

    return () => {
      window.cancelAnimationFrame(animationFrame)
      window.clearTimeout(shortDelay)
      window.clearTimeout(galleryAnimationDelay)
    }
  }, [chart?.revision, fitOrgChartView, nodeIdentity, reactFlowInstance])

  const saveMutation = useMutation({
    mutationFn: (state: { nodes: Node<AgentOrgChartNodeData>[]; edges: Edge[] }) => {
      if (!chart) {
        throw new Error('Org chart has not loaded yet.')
      }
      return saveAgentOrgChart({
        revision: chart.revision,
        viewport: reactFlowInstance?.getViewport() ?? chart.viewport,
        nodes: state.nodes.map((node) => ({
          agentId: node.id,
          x: node.position.x,
          y: node.position.y,
        })),
        edges: state.edges.map((edge) => ({
          parentAgentId: edge.source,
          childAgentId: edge.target,
        })),
      })
    },
    onSuccess: (payload) => {
      setDirty(false)
      void queryClient.setQueryData(chartQueryKey, payload)
      void queryClient.invalidateQueries({ queryKey: chartQueryKey })
    },
  })

  const persistState = useCallback(
    (nextNodes: Node<AgentOrgChartNodeData>[], nextEdges: Edge[]) => {
      if (!chart || saveMutation.isPending) {
        return
      }
      saveMutation.mutate({ nodes: nextNodes, edges: nextEdges })
    },
    [chart, saveMutation],
  )

  const onNodesChange = useCallback((changes: NodeChange<Node<AgentOrgChartNodeData>>[]) => {
    setNodes((currentNodes) => applyNodeChanges<Node<AgentOrgChartNodeData>>(changes, currentNodes))
    if (changes.some((change) => change.type === 'position' && change.dragging === false)) {
      setDirty(true)
    }
  }, [])

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((currentEdges) => applyEdgeChanges(changes, currentEdges))
  }, [])

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || connection.source === connection.target) {
      return
    }
    if (edges.some((edge) => edge.source === connection.source && edge.target === connection.target)) {
      return
    }

    const existingParentEdge = edges.find((edge) => edge.target === connection.target)
    const candidateEdges = existingParentEdge
      ? edges.filter((edge) => edge.id !== existingParentEdge.id)
      : edges

    if (connectionCreatesCycle(candidateEdges, connection.source, connection.target)) {
      window.alert('That connection would create a loop in the org chart.')
      return
    }

    if (existingParentEdge) {
      const sourceAgent = agents.find((agent) => agent.id === connection.source)
      const targetAgent = agents.find((agent) => agent.id === connection.target)
      const confirmed = window.confirm(
        `Replace ${targetAgent?.name || 'this agent'}'s current manager with ${sourceAgent?.name || 'this agent'}? This also changes agent-to-agent messaging access.`,
      )
      if (!confirmed) {
        return
      }
    }

    const nextEdges = addEdge(
      {
        ...connection,
        id: `${connection.source}:${connection.target}`,
        type: 'smoothstep',
        markerEnd: { type: MarkerType.ArrowClosed },
      },
      candidateEdges,
    )
    setEdges(nextEdges)
    setDirty(true)
    persistState(nodes, nextEdges)
  }, [agents, edges, nodes, persistState])

  const handleSave = useCallback(() => {
    persistState(nodes, edges)
  }, [edges, nodes, persistState])

  const handleAutoLayout = useCallback(() => {
    void layoutNodes(nodes, edges).then((layoutedNodes) => {
      setNodes(layoutedNodes)
      setDirty(true)
      window.setTimeout(() => fitOrgChartView(160), 0)
    })
  }, [edges, fitOrgChartView, nodes])

  const handleDeleteSelectedEdge = useCallback(() => {
    if (!selectedEdgeId) {
      return
    }
    const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId)
    if (!selectedEdge) {
      return
    }
    const confirmed = window.confirm('Remove this org chart connection and disable agent-to-agent messaging for it?')
    if (!confirmed) {
      return
    }
    const nextEdges = edges.filter((edge) => edge.id !== selectedEdgeId)
    setEdges(nextEdges)
    setSelectedEdgeId(null)
    setDirty(true)
    persistState(nodes, nextEdges)
  }, [edges, nodes, persistState, selectedEdgeId])

  const statusMessage = useMemo(() => {
    if (chartQuery.isLoading) {
      return 'Loading org chart...'
    }
    if (chartQuery.isError) {
      return 'Unable to load org chart.'
    }
    if (saveMutation.isPending) {
      return 'Saving...'
    }
    if (saveMutation.isError) {
      return getErrorMessage(saveMutation.error)
    }
    if (dirty) {
      return 'Unsaved layout changes'
    }
    return 'Saved'
  }, [chartQuery.isError, chartQuery.isLoading, dirty, saveMutation.error, saveMutation.isError, saveMutation.isPending])

  return (
    <div className="agent-org-chart">
      <div className="agent-org-chart-toolbar">
        <span className="agent-org-chart-toolbar-status">{statusMessage}</span>
        <div className="agent-org-chart-toolbar-actions">
          <button
            type="button"
            className="agent-org-chart-toolbar-button"
            onClick={handleAutoLayout}
            disabled={nodes.length === 0}
            title="Auto layout"
            aria-label="Auto layout"
          >
            <Building2 className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="agent-org-chart-toolbar-button"
            onClick={() => chartQuery.refetch()}
            title="Reload"
            aria-label="Reload"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="agent-org-chart-toolbar-button"
            onClick={handleDeleteSelectedEdge}
            disabled={!selectedEdgeId}
            title="Delete connection"
            aria-label="Delete connection"
          >
            <Trash2 className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="agent-org-chart-toolbar-button agent-org-chart-toolbar-button--primary"
            onClick={handleSave}
            disabled={!dirty || saveMutation.isPending || !chart}
            title="Save layout"
            aria-label="Save layout"
          >
            <Save className="h-4 w-4" />
          </button>
        </div>
      </div>
      {chart?.unplacedPeerLinks.length ? (
        <div className="agent-org-chart-unplaced">
          {chart.unplacedPeerLinks.length} peer link{chart.unplacedPeerLinks.length === 1 ? '' : 's'} need manual placement
        </div>
      ) : null}
      <div className="agent-org-chart-canvas">
        <ReactFlow<Node<AgentOrgChartNodeData>, Edge>
          nodes={nodes}
          edges={edges.map((edge) => ({
            ...edge,
            selected: edge.id === selectedEdgeId,
          }))}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onInit={setReactFlowInstance}
          onEdgeClick={(_, edge) => setSelectedEdgeId(edge.id)}
          onPaneClick={() => setSelectedEdgeId(null)}
          nodesDraggable
          nodesConnectable
          connectionMode={ConnectionMode.Loose}
          connectionRadius={125}
          edgesFocusable
          deleteKeyCode={null}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="rgba(196, 181, 253, 0.18)" gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}

export function AgentOrgChartView(props: AgentOrgChartViewProps) {
  return (
    <ReactFlowProvider>
      <AgentOrgChartCanvas {...props} />
    </ReactFlowProvider>
  )
}
