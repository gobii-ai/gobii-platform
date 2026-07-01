export type AgentFsNode = {
  id: string
  parentId: string | null
  name: string
  path: string
  nodeType: 'dir' | 'file'
  sizeBytes: number | null
  updatedAt: string | null
}

export type AgentFilesResponse = {
  nodes: AgentFsNode[]
}

export type AgentFilesPageData = {
  csrfToken: string
  agent: {
    id: string
    name: string
  }
  backLink: {
    url: string
    label: string
  }
  permissions: {
    canManage: boolean
  }
  urls: {
    files: string
    upload: string
    delete: string
    download: string
    createFolder: string
    move: string
  }
}
