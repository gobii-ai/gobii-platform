export type AgentFsNode = {
  id: string
  parentId: string | null
  name: string
  path: string
  nodeType: 'dir' | 'file'
  sizeBytes: number | null
  mimeType: string | null
  createdAt: string | null
  updatedAt: string | null
}

export type AgentFilesResponse = {
  filespace: {
    id: string
    name: string
  }
  nodes: AgentFsNode[]
}

export type AgentFilesPageData = {
  csrfToken: string
  agent: {
    id: string
    name: string
  }
  urls: {
    agentDetail: string
    files: string
    upload: string
    delete: string
    download: string
    createFolder: string
    move: string
  }
}
