import fs from 'node:fs';
import YAML from 'yaml';

const specPath = new URL('../static/openapi/GobiiAPI.yaml', import.meta.url);

const operationTitles = {
  listPersistentAgents: 'Get agents',
  createPersistentAgent: 'Create an Agent',
  getPersistentAgent: 'Get Agent',
  updatePersistentAgent: 'Replace Persistent Agent',
  partialUpdatePersistentAgent: 'Patch Persistent Agent',
  deletePersistentAgent: 'Delete Agent',
  activatePersistentAgent: 'Activate Agent',
  deactivatePersistentAgent: 'Deactivate Agent',
  sendPersistentAgentMessage: 'Message Agent',
  getPersistentAgentProcessingStatus: 'Get Agent Processing Status',
  previewPersistentAgentSchedule: 'Preview Agent Schedule',
  getPersistentAgentTimeline: 'Get Agent Timeline',
  listWebTasks: 'List Web Tasks',
  listAgents: 'List Legacy Browser-Use Agents',
  createAgent: 'Create Legacy Browser-Use Agent',
  listTasks: 'List Legacy Agent Tasks',
  assignTask: 'Create Legacy Agent Task',
  getTask: 'Get Legacy Agent Task',
  updateTask: 'Replace Legacy Agent Task',
  updateTaskStatusPartial: 'Patch Legacy Agent Task',
  deleteTask: 'Delete Legacy Agent Task',
  cancelTask: 'Cancel Legacy Agent Task',
  getTaskResult: 'Get Legacy Agent Task Result',
  getAgent: 'Get Legacy Browser-Use Agent',
  updateAgent: 'Replace Legacy Browser-Use Agent',
  updateAgentStatusPartial: 'Patch Legacy Browser-Use Agent',
  deleteAgent: 'Delete Legacy Browser-Use Agent',
  ping: 'Ping API',
  listAllTasks: 'List Standalone Legacy Tasks',
  assignTask2: 'Create Standalone Legacy Task',
  getTask2: 'Get Standalone Legacy Task',
  updateTask2: 'Replace Standalone Legacy Task',
  updateTaskStatusPartial2: 'Patch Standalone Legacy Task',
  deleteTask2: 'Delete Standalone Legacy Task',
  cancelTask2: 'Cancel Standalone Legacy Task',
  getTaskResult2: 'Get Standalone Legacy Task Result',
};

const operationSidebarTitles = {
  updatePersistentAgent: 'Agent - Replace',
  partialUpdatePersistentAgent: 'Agent - Patch',
  listAgents: 'Legacy Browser-Use Agents - List',
  createAgent: 'Legacy Browser-Use Agent - Create',
  listTasks: 'Legacy Agent Tasks - List',
  assignTask: 'Legacy Agent Task - Create',
  getTask: 'Legacy Agent Task - Get',
  updateTask: 'Legacy Agent Task - Replace',
  updateTaskStatusPartial: 'Legacy Agent Task - Patch',
  deleteTask: 'Legacy Agent Task - Delete',
  cancelTask: 'Legacy Agent Task - Cancel',
  getTaskResult: 'Legacy Agent Task Result - Get',
  getAgent: 'Legacy Browser-Use Agent - Get',
  updateAgent: 'Legacy Browser-Use Agent - Replace',
  updateAgentStatusPartial: 'Legacy Browser-Use Agent - Patch',
  deleteAgent: 'Legacy Browser-Use Agent - Delete',
  listAllTasks: 'Standalone Legacy Tasks - List',
  assignTask2: 'Standalone Legacy Task - Create',
  getTask2: 'Standalone Legacy Task - Get',
  updateTask2: 'Standalone Legacy Task - Replace',
  updateTaskStatusPartial2: 'Standalone Legacy Task - Patch',
  deleteTask2: 'Standalone Legacy Task - Delete',
  cancelTask2: 'Standalone Legacy Task - Cancel',
  getTaskResult2: 'Standalone Legacy Task Result - Get',
};

const text = fs.readFileSync(specPath, 'utf8');
const doc = YAML.parseDocument(text);
const root = doc.toJS();

root.info = {
  ...root.info,
  description:
    'REST API reference for Gobii persistent agents, legacy browser-use automation tasks, webhooks, authentication, request parameters, and response schemas.',
};

root.tags = [
  { name: 'Agents API', description: 'Persistent Gobii agent endpoints for creating, scheduling, messaging, and managing Gobiis through the API.' },
  { name: 'browser-use Tasks API', description: 'Legacy browser-use agent and task endpoints for submitting browser automation jobs, polling status, and retrieving results.' },
  { name: 'Utilities', description: 'Utility endpoints for health checks and simple Gobii API integration verification.' },
];

for (const [operationPath, pathItem] of Object.entries(root.paths ?? {})) {
  for (const [method, operation] of Object.entries(pathItem ?? {})) {
    if (!operation || typeof operation !== 'object' || !operation.operationId) {
      continue;
    }

    const title = operationTitles[operation.operationId];
    if (title) {
      operation.summary = title;
    }

    const sidebarTitle = operationSidebarTitles[operation.operationId];
    if (sidebarTitle) {
      operation['x-mint'] = {
        ...(operation['x-mint'] ?? {}),
        metadata: {
          ...(operation['x-mint']?.metadata ?? {}),
          sidebarTitle,
        },
      };
    }

    const description = typeof operation.description === 'string' ? operation.description.trim() : '';
    const genericDescription = /ViewSet|Override create/i.test(description);
    const generatedDescription = / with the Gobii REST API endpoint [A-Z]+ .+ Includes authentication, parameters, request body, response schema, and examples for persistent agents and legacy browser-use automation tasks\.$/.test(description);
    if (!description || description.length < 70 || genericDescription || generatedDescription) {
      operation.description = `${title || operation.operationId} with the Gobii REST API endpoint ${method.toUpperCase()} ${operationPath}. Includes authentication, parameters, request body, response schema, and examples for persistent agents and legacy browser-use automation tasks.`;
    } else {
      operation.description = description
        .replace(/AI browser agents, browser-use automation tasks/g, 'persistent agents, legacy browser-use automation tasks')
        .replace(/AI browser agents and browser-use automation tasks/g, 'persistent agents and legacy browser-use automation tasks')
        .replace(/AI browser agents/g, 'persistent agents');
    }
  }
}

doc.contents = root;
fs.writeFileSync(specPath, `${doc.toString({ lineWidth: 0 })}`);
