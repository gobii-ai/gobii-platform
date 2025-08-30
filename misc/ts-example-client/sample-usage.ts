// Sample usage of the generated TypeScript client

import { BrowserUseAgentsApi, Configuration } from './src/generated';

// Create client with API key
const api = new BrowserUseAgentsApi({
  // The base URL defaults to https://gobii.ai/api/v1
  // Override only if needed:
  // basePath: 'https://dev.example.com/api/v1',
  headers: { 
    'X-Api-Key': 'your-api-key-here' 
  }
});

// Example usage of the client API
async function example() {
  try {
    // Create an agent
    console.log("Creating agent...");
    const agent = await api.browserUseAgentsCreateAgent({ 
      name: 'my-test-agent' 
    });
    console.log(`Agent created with ID: ${agent.id}`);
    
    // Assign a task
    console.log("Assigning task...");
    const task = await api.browserUseAgentsAssignTask({ 
      agentId: agent.id,
      inputData: JSON.stringify({
        url: "https://example.com", 
        action: "screenshot"
      })
    });
    console.log(`Task assigned with ID: ${task.id}`);
    
    // Poll for task result
    console.log("Waiting for task to complete...");
    let taskResult;
    let attempts = 0;
    const maxAttempts = 10;
    
    while (attempts < maxAttempts) {
      taskResult = await api.browserUseAgentsGetTaskResult({ 
        agentId: agent.id,
        id: task.id 
      });
      
      if (taskResult.status === 'completed' || 
          taskResult.status === 'failed' || 
          taskResult.status === 'cancelled') {
        break;
      }
      
      console.log(`Task status: ${taskResult.status}. Waiting...`);
      await new Promise(resolve => setTimeout(resolve, 5000)); // Wait 5 seconds
      attempts++;
    }
    
    // Show final result
    if (taskResult.status === 'completed') {
      console.log("Task completed successfully!");
      console.log("Result:", taskResult.result);
    } else {
      console.log(`Task ended with status: ${taskResult.status}`);
      if (taskResult.errorMessage) {
        console.log("Error:", taskResult.errorMessage);
      }
    }
    
  } catch (error) {
    console.error("Error during API operations:", error);
  }
}

// Run the example
example().catch(console.error);