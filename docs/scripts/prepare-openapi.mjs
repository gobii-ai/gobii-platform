import fs from 'node:fs';
import YAML from 'yaml';

const specPath = new URL('../static/openapi/GobiiAPI.yaml', import.meta.url);

const operationTitles = {
  listPersistentAgents: 'Get agents',
  createPersistentAgent: 'Create an Agent',
  getPersistentAgent: 'Get Agent',
  updatePersistentAgent: 'Update Agent',
  partialUpdatePersistentAgent: 'Update Agent',
  deletePersistentAgent: 'Delete Agent',
  activatePersistentAgent: 'Activate Agent',
  deactivatePersistentAgent: 'Deactivate Agent',
  sendPersistentAgentMessage: 'Message Agent',
  getPersistentAgentProcessingStatus: 'Get Agent Processing Status',
  previewPersistentAgentSchedule: 'Preview Agent Schedule',
  getPersistentAgentTimeline: 'Get Agent Timeline',
  listWebTasks: 'List Web Tasks',
  listAgents: 'List browser-use Profiles',
  createAgent: 'Create browser-use Profile',
  listTasks: 'List Tasks',
  assignTask: 'Create Task',
  getTask: 'Get Task',
  updateTask: 'Update Task',
  updateTaskStatusPartial: 'Update Task',
  deleteTask: 'Delete Task',
  cancelTask: 'Cancel Task',
  getTaskResult: 'Get Task Result',
  getAgent: 'Get browser-use Profile',
  updateAgent: 'Update browser-use Profile',
  updateAgentStatusPartial: 'Update browser-use Profile',
  deleteAgent: 'Delete browser-use Profile',
  ping: 'Ping API',
  listAllTasks: 'List browser-use Tasks',
  assignTask2: 'Create Task',
  getTask2: 'Get Task',
  updateTask2: 'Update Task',
  updateTaskStatusPartial2: 'Update Task',
  deleteTask2: 'Delete Task',
  cancelTask2: 'Cancel Task',
  getTaskResult2: 'Get Task Result',
};

const text = fs.readFileSync(specPath, 'utf8');
const doc = YAML.parseDocument(text);
const root = doc.toJS();

root.tags = [
  { name: 'Agents API' },
  { name: 'browser-use Tasks API' },
  { name: 'Utilities' },
];

for (const pathItem of Object.values(root.paths ?? {})) {
  for (const operation of Object.values(pathItem ?? {})) {
    if (!operation || typeof operation !== 'object' || !operation.operationId) {
      continue;
    }

    const title = operationTitles[operation.operationId];
    if (title) {
      operation.summary = title;
    }
  }
}

doc.contents = root;
fs.writeFileSync(specPath, `${doc.toString({ lineWidth: 0 })}`);
