import { Command } from 'commander';
import { v4 as uuidv4 } from 'uuid';
import dotenv from 'dotenv';
import {
    Configuration,
    AgentsApi,
    BrowserUseApi,
    TasksApi,
    AgentDetail,
    PaginatedAgentListList,
    TaskDetail,
    PaginatedTaskListList,
    TaskList,
    CancelTaskResponse
} from '@gobii-ai/client';

// Load environment variables from .env file
dotenv.config();

// --- Configuration ---
const GOBII_API_KEY = process.env.GOBII_API_KEY;
const DEFAULT_API_BASE_URL =
    process.env.API_BASE_URL ||
    process.env.GOBII_API_BASE_URL ||
    process.env.PUBLIC_SITE_URL ||
    (process.env.GOBII_PROPRIETARY_MODE === 'true'
        ? 'https://gobii.ai'
        : 'http://localhost:8000');

function resolveBaseUrl(provided?: string): string {
    return provided || DEFAULT_API_BASE_URL;
}

// --- Helper Functions ---
function generateRandomString(prefix: string): string {
    return `${prefix}-${uuidv4().slice(0, 8)}`;
}

function prettyPrintJson(data: unknown): string {
    return JSON.stringify(data, null, 2);
}

async function handleApiCall<T>(
    operationName: string,
    apiCall: () => Promise<T>
): Promise<T | null> {
    console.log(`--- Running: ${operationName} ---`);
    try {
        const result = await apiCall();
        console.log('API Call Successful.');
        console.log(`Result:\n${prettyPrintJson(result)}`);
        return result;
    } catch (error: any) {
        
        console.error(`Error during ${operationName}:`);
        
        if (error.response) {
            console.error(`  Status: ${error.response.status}`);
            console.error(`  Status Text: ${error.response.statusText}`);
            
            try {
                if (typeof error.response.clone === 'function') {
                    const clonedResponse = error.response.clone();
                    const errorText = await clonedResponse.text();
                    try {
                        const errorJson = JSON.parse(errorText);
                        console.error(`  Response Body:\n${prettyPrintJson(errorJson)}`);
                    } catch {
                        console.error(`  Response Body (raw): ${errorText}`);
                    }
                } else if (error.response.data) {
                    console.error(`  Response Body:\n${prettyPrintJson(error.response.data)}`);
                } else {
                    console.error('  Response body unavailable or already consumed');
                }
            } catch (parseError: any) {
                console.error('  Could not read response body:', parseError.message);
            }
        } else if (error.request) {
            console.error('  No response received from server.', error.message);
        } else {
            console.error('  Error setting up request:', error.message);
        }
        return null;
    }
}

// --- API Client Initialization ---
let agentsApi: AgentsApi;
let browserUseApi: BrowserUseApi;
let tasksApi: TasksApi;

function initializeApiClient(apiKey: string, basePath?: string) {
    if (!apiKey) {
        console.error(`Error: API Key not found. Set the ${process.env.GOBII_API_KEY_ENV_VAR || 'GOBII_API_KEY'} environment variable.`);
        process.exit(1);
    }

    // Create the configuration object
    const configOptions: any = {
        headers: {
            'X-Api-Key': apiKey
        }
    };

    // If basePath is provided, override the default path
    if (basePath) {
        configOptions.basePath = `${basePath}/api/v1`;
    }

    const config = new Configuration(configOptions);

    agentsApi = new AgentsApi(config);
    browserUseApi = new BrowserUseApi(config);
    tasksApi = new TasksApi(config);
    console.log("API Client Initialized.");
}

// --- API Operation Functions ---

async function createAgent(agentName: string): Promise<AgentDetail | null> {
    console.log(`Input Agent Name: ${agentName}`);
    return handleApiCall('Create Agent', () =>
        browserUseApi.createAgent({ name: agentName })
    );
}

async function listAgents(): Promise<PaginatedAgentListList | null> {
    return handleApiCall('List Agents', () => 
        browserUseApi.listAgents()
    );
}

async function getAgent(agentId: string): Promise<AgentDetail | null> {
    console.log(`Input Agent ID: ${agentId}`);
    return handleApiCall('Get Agent Details', () =>
        browserUseApi.getAgent(agentId)
    );
}

async function updateAgentName(
    agentId: string,
    newName: string
): Promise<AgentDetail | null> {
    console.log(`Input Agent ID: ${agentId}`);
    console.log(`Input New Name: ${newName}`);
    return handleApiCall('Update Agent Name', () =>
        browserUseApi.updateAgent(agentId, { name: newName })
    );
}

async function deleteAgent(agentId: string): Promise<boolean> {
    console.log(`Input Agent ID: ${agentId}`);
    const result = await handleApiCall('Delete Agent', () =>
        browserUseApi.deleteAgent(agentId)
    );
    return result !== null;
}

async function assignTask(
    agentId: string,
    taskInput: string
): Promise<TaskDetail | null> {
    console.log(`Input Agent ID: ${agentId}`);
    console.log(`Input Task Data: ${taskInput}`);
    return handleApiCall('Assign Task', () =>
        browserUseApi.assignTask(agentId, { 
            inputData: taskInput
        })
    );
}

async function listTasksForAgent(agentId: string): Promise<PaginatedTaskListList | null> {
    console.log(`Input Agent ID: ${agentId}`);
    return handleApiCall('List Tasks for Agent', () =>
        browserUseApi.listTasks(agentId)
    );
}

// The API now directly exposes a method to list all tasks for a user
async function listAllTasks(): Promise<TaskList | null> {
    return handleApiCall('List All Tasks for User', () => 
        browserUseApi.listAllTasks()
    );
}

async function getTask(
    agentId: string,
    taskId: string
): Promise<TaskDetail | null> {
    console.log(`Input Agent ID: ${agentId}`);
    console.log(`Input Task ID: ${taskId}`);
    return handleApiCall('Get Task Details', () =>
        browserUseApi.getTask(agentId, taskId)
    );
}

async function updateTask(
    agentId: string,
    taskId: string,
    newInputData: string
): Promise<TaskDetail | null> {
    console.log(`Input Agent ID: ${agentId}`);
    console.log(`Input Task ID: ${taskId}`);
    console.log(`Input New Data: ${newInputData}`);
    return handleApiCall('Update Task Input Data', () =>
        browserUseApi.updateTask(agentId, taskId, { inputData: newInputData })
    );
}

async function cancelTask(agentId: string, taskId: string): Promise<CancelTaskResponse | null> {
    console.log(`Input Agent ID: ${agentId}`);
    console.log(`Input Task ID: ${taskId}`);
    const result = await handleApiCall('Cancel Task', () =>
        browserUseApi.cancelTask(agentId, taskId)
    );
    return result;
}

async function deleteTask(agentId: string, taskId: string): Promise<boolean> {
    console.log(`Input Agent ID: ${agentId}`);
    console.log(`Input Task ID: ${taskId}`);
    const result = await handleApiCall('Delete Task', () =>
        browserUseApi.deleteTask(agentId, taskId)
    );
    return result !== null;
}

// --- Demo Flow ---
async function runAllDemo(apiKey: string, baseUrl: string) {
    initializeApiClient(apiKey, baseUrl);
    console.log('\n=== Running Full Demo ===\n');

    const agentName = generateRandomString('ts-demo-agent');
    const createdAgent = await createAgent(agentName);

    if (!createdAgent?.id) {
        console.error('Demo failed: Could not create agent.');
        return;
    }
    const agentId = createdAgent.id;

    await listAgents();
    await getAgent(agentId);

    const taskInput1 = `Task 1 for ${agentName}`;
    const createdTask = await assignTask(agentId, taskInput1);

    if (!createdTask?.id) {
        console.error('Demo failed: Could not assign task.');
        await deleteAgent(agentId); // Clean up agent
        return;
    }
    const taskId = createdTask.id;

    await getTask(agentId, taskId);
    await listTasksForAgent(agentId);
    await listAllTasks();

    await updateTask(agentId, taskId, `Updated task input for ${agentName}`);
    await getTask(agentId, taskId);

    const cancelResult = await cancelTask(agentId, taskId);
    if (cancelResult) {
      console.log('Task cancellation initiated (or completed).');
      await getTask(agentId, taskId);
    }

    await deleteAgent(agentId);
    await getAgent(agentId);

    console.log('\n=== Demo Finished ===\n');
}

// --- CLI Setup ---
async function main() {
    const program = new Command();

    program
        .name('gobii-ts-example-client')
        .description('Example TypeScript client for the Gobii API')
        .version('0.1.0');

    program
        .option('-k, --api-key <key>', 'Gobii API Key', GOBII_API_KEY)
        .option(
            '-b, --base-url <url>',
            `API Base URL (default: ${DEFAULT_API_BASE_URL})`,
            DEFAULT_API_BASE_URL,
        )
        .option('--task-id <id>', 'Task ID')
        .option('-w, --wait <seconds>', 'Wait for task completion (0-900 seconds)', parseInt)
        .option('-o, --output-schema <schema>', 'JSON schema for output validation');

    program
        .command('create-agent')
        .description('Create a new agent')
        .argument('<n>', 'Name for the new agent')
        .action(async (name: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await createAgent(name);
        });

    program
        .command('list-agents')
        .description('List all agents')
        .action(async (options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await listAgents();
        });

    program
        .command('get-agent')
        .description('Get details for a specific agent')
        .argument('<agentId>', 'ID of the agent')
        .action(async (agentId: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await getAgent(agentId);
        });

    program
        .command('update-agent')
        .description('Update the name of an agent')
        .argument('<agentId>', 'ID of the agent to update')
        .argument('<newName>', 'New name for the agent')
        .action(async (agentId: string, newName: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await updateAgentName(agentId, newName);
        });

    program
        .command('delete-agent')
        .description('Delete an agent')
        .argument('<agentId>', 'ID of the agent to delete')
        .action(async (agentId: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await deleteAgent(agentId);
        });

    program
        .command('assign-task')
        .description('Assign a task to an agent')
        .argument('<agentId>', 'ID of the agent')
        .argument('<inputData>', 'Input data for the task')
        .action(async (agentId: string, inputData: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await assignTask(agentId, inputData);
        });

    program
        .command('list-tasks')
        .description('List tasks for a specific agent')
        .argument('<agentId>', 'ID of the agent')
        .action(async (agentId: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await listTasksForAgent(agentId);
        });

    program
        .command('list-all-tasks')
        .description('List all tasks for the authenticated user')
        .action(async (options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await listAllTasks();
        });

    program
        .command('get-task')
        .description('Get details for a specific task')
        .argument('<agentId>', 'ID of the agent')
        .argument('<taskId>', 'ID of the task')
        .action(async (agentId: string, taskId: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await getTask(agentId, taskId);
        });

    program
        .command('update-task')
        .description('Update the input data for a task')
        .argument('<agentId>', 'ID of the agent')
        .argument('<taskId>', 'ID of the task')
        .argument('<newInputData>', 'New input data for the task')
        .action(async (agentId: string, taskId: string, newInputData: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await updateTask(agentId, taskId, newInputData);
        });

    program
        .command('cancel-task')
        .description('Cancel a specific task')
        .argument('<agentId>', 'ID of the agent')
        .argument('<taskId>', 'ID of the task')
        .action(async (agentId: string, taskId: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await cancelTask(agentId, taskId);
        });

    program
        .command('delete-task')
        .description('Delete a specific task')
        .argument('<agentId>', 'ID of the agent')
        .argument('<taskId>', 'ID of the task')
        .action(async (agentId: string, taskId: string, options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            await deleteTask(agentId, taskId);
        });

    program
        .command('run-all')
        .description('Run a sequence of demo operations')
        .action(async (options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            await runAllDemo(opts.apiKey, baseUrl);
        });

    program
        .command('ping')
        .description('Test API connectivity')
        .action(async (options: any) => {
            const opts = { ...program.opts(), ...options };
            const baseUrl = resolveBaseUrl(opts.baseUrl);
            initializeApiClient(opts.apiKey, baseUrl);
            try {
                // Use direct fetch for ping endpoint
                const pingUrl = `${baseUrl}/api/v1/ping/`;
                
                console.log(`Testing connection to ${pingUrl}`);
                const response = await fetch(pingUrl, {
                    headers: {
                        'X-Api-Key': opts.apiKey
                    }
                });
                
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                const result = await response.json();
                console.log("✅ Successfully connected to API!");
                console.log(result);
            } catch (error) {
                console.error("❌ Failed to connect to API");
                console.error(error);
            }
        });

    await program.parseAsync(process.argv);
}

main().catch(error => {
    console.error("Unhandled error in main execution:", error);
    process.exit(1);
}); 
