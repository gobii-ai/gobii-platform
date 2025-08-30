"""
Example Python client for the Gobii API.

This script demonstrates how to:
1. Retrieve an API key from an environment variable.
2. Create a 'browser-use' agent.
3. Assign a task to the created agent.
4. Delete an agent.
5. Get task status/result.
6. List agents.
7. Get a specific agent's details.
8. Update an agent's name.
9. List tasks for an agent.
10. List all tasks for the authenticated user.
11. Update a task's input data.
12. Cancel a task.
13. Soft delete a task.

It provides verbose output for requests and responses.
"""
import os
import requests
import json # For pretty printing JSON, requests handles most (de)serialization
import argparse
import uuid
import sys

# Configuration
DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1/" # Ensure trailing slash
GOBII_API_KEY_ENV_VAR = "GOBII_API_KEY"

def generate_random_string(prefix: str) -> str:
    """Generates a random string with a given prefix and a UUID suffix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

def create_agent(api_key: str, base_url: str, agent_name: str) -> str | None:
    """
    Creates a new 'browser-use' agent.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_name: The name for the new agent.

    Returns:
        The ID of the created agent if successful, otherwise None.
    """
    url = f"{base_url}agents/browser-use/"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }
    payload = {"name": agent_name}

    print(f"--- Creating Agent: {agent_name} ---")
    print(f"Request: POST {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")
    print(f"Body: {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        response_json = {}
        try:
            response_json = response.json()
            print(f"Response Body: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (Not JSON): {response.text}")


        if response.status_code == 201: # HTTP 201 Created
            agent_id = response_json.get("id")
            if agent_id:
                print(f"Agent '{agent_name}' created successfully. Agent ID: {agent_id}")
                return agent_id
            else:
                print("Error: Agent created but ID not found in response.")
                return None
        else:
            print(f"Error creating agent. Status: {response.status_code}, Details: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to create agent: {e}")
        return None

def assign_task(api_key: str, base_url: str, agent_id: str, task_input: str) -> dict | None:
    """
    Assigns a new task to an existing agent.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent to assign the task to.
        task_input: The input data (description) for the task.

    Returns:
        The JSON response dictionary representing the created task if successful, otherwise None.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/tasks/"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }
    payload = {"prompt": task_input}

    print(f"--- Assigning Task to Agent ID: {agent_id} ---")
    print(f"Task Input: {task_input}")
    print(f"Request: POST {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")
    print(f"Body: {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        response_json = {}
        try:
            response_json = response.json()
            print(f"Response Body: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (Not JSON): {response.text}")


        if response.status_code == 201: # HTTP 201 Created
            print(f"Task assigned successfully to agent {agent_id}.")
            return response_json
        else:
            print(f"Error assigning task. Status: {response.status_code}, Details: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to assign task: {e}")
        return None

def delete_agent_cli(api_key: str, base_url: str, agent_id: str) -> bool:
    """
    Deletes an agent by its ID.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent to delete.

    Returns:
        True if the agent was deleted successfully (HTTP 204), False otherwise.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/"
    headers = {"X-Api-Key": api_key}

    print(f"--- Deleting Agent ID: {agent_id} ---")
    print(f"Request: DELETE {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")

    try:
        response = requests.delete(url, headers=headers, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        # For DELETE 204, there's usually no body, or it might be empty.
        if response.text:
            try:
                # Try to print as JSON if possible, otherwise raw text
                response_json = response.json()
                print(f"Response Body: {json.dumps(response_json, indent=2)}")
            except json.JSONDecodeError:
                print(f"Response Body (Not JSON): {response.text}")
        else:
            print("Response Body: (No content)")


        if response.status_code == 204:  # HTTP 204 No Content
            print(f"Agent '{agent_id}' deleted successfully.")
            return True
        else:
            print(f"Error deleting agent {agent_id}. Status: {response.status_code}, Details: {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Error making request to delete agent {agent_id}: {e}")
        return False

def get_task_cli(api_key: str, base_url: str, agent_id: str, task_id: str) -> dict | None:
    """
    Retrieves the status/result of a specific task.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent the task belongs to.
        task_id: The ID of the task to retrieve.

    Returns:
        The parsed JSON response as a dictionary if successful (HTTP 200), otherwise None.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/tasks/{task_id}/result/"
    headers = {"X-Api-Key": api_key}

    print(f"--- Getting Task Details for Task ID: {task_id} (Agent ID: {agent_id}) ---")
    print(f"Request: GET {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Response Status Code: {response.status_code}")

        if response.status_code == 200:  # HTTP 200 OK
            try:
                response_json = response.json()
                print("Response Body (JSON):")
                print(json.dumps(response_json, indent=2))
                print(f"Successfully retrieved task details for Task ID: {task_id}.")
                return response_json
            except json.JSONDecodeError:
                print("Error: Failed to parse response as JSON.")
                print(f"Response Body (Raw Text): {response.text}")
                return None
        else:
            print(f"Error retrieving task details. Status: {response.status_code}")
            if response.text:
                try:
                    response_json = response.json()
                    print(f"Response Body (Error JSON): {json.dumps(response_json, indent=2)}")
                except json.JSONDecodeError:
                    print(f"Response Body (Error Raw Text): {response.text}")
            else:
                print("Response Body: (No content)")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to get task details for Task ID {task_id}: {e}")
        return None

def list_agents_cli(api_key: str, base_url: str) -> list | None:
    """
    Lists all 'browser-use' agents.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.

    Returns:
        A list of agents (parsed JSON) on success (HTTP 200), None otherwise.
    """
    url = f"{base_url}agents/browser-use/"
    headers = {"X-Api-Key": api_key}

    print(f"--- Listing Agents ---")
    print(f"Request: GET {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        response_json = {}
        try:
            response_json = response.json()
            print(f"Response Body: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (Not JSON): {response.text}")
            if response.status_code == 200: # If successful but not JSON, something is wrong
                 return None

        if response.status_code == 200:
            print("Successfully listed agents.")
            return response_json # Assuming response.json() directly returns a list
        else:
            print(f"Error listing agents. Status: {response.status_code}, Details: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to list agents: {e}")
        return None

def get_agent_cli(api_key: str, base_url: str, agent_id: str) -> dict | None:
    """
    Retrieves details of a specific 'browser-use' agent.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent to retrieve.

    Returns:
        The agent details (parsed JSON) on success (HTTP 200), None otherwise.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/"
    headers = {"X-Api-Key": api_key}

    print(f"--- Getting Agent Details for ID: {agent_id} ---")
    print(f"Request: GET {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        response_json = {}
        try:
            response_json = response.json()
            print(f"Response Body: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (Not JSON): {response.text}")
            if response.status_code == 200:
                return None

        if response.status_code == 200:
            print(f"Successfully retrieved details for agent ID: {agent_id}.")
            return response_json
        else:
            print(f"Error retrieving agent {agent_id}. Status: {response.status_code}, Details: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to get agent {agent_id}: {e}")
        return None

def update_agent_name_cli(api_key: str, base_url: str, agent_id: str, new_name: str) -> dict | None:
    """
    Updates the name of a specific 'browser-use' agent.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent to update.
        new_name: The new name for the agent.

    Returns:
        The updated agent details (parsed JSON) on success (HTTP 200), None otherwise.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }
    payload = {"name": new_name}

    print(f"--- Updating Agent Name for ID: {agent_id} ---")
    print(f"New Name: {new_name}")
    print(f"Request: PATCH {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")
    print(f"Body: {json.dumps(payload, indent=2)}")

    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        response_json = {}
        try:
            response_json = response.json()
            print(f"Response Body: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (Not JSON): {response.text}")
            if response.status_code == 200:
                return None

        if response.status_code == 200:
            print(f"Agent '{agent_id}' updated successfully to name '{new_name}'.")
            return response_json
        else:
            print(f"Error updating agent {agent_id}. Status: {response.status_code}, Details: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to update agent {agent_id}: {e}")
        return None

def list_tasks_for_agent_cli(api_key: str, base_url: str, agent_id: str) -> list | None:
    """
    Lists tasks for a specific agent.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent whose tasks are to be listed.

    Returns:
        A list of tasks (parsed JSON) on success (HTTP 200), None otherwise.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/tasks/"
    headers = {"X-Api-Key": api_key}

    print(f"--- Listing Tasks for Agent ID: {agent_id} ---")
    print(f"Request: GET {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        response_json = {}
        try:
            response_json = response.json()
            print(f"Response Body: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (Not JSON): {response.text}")
            if response.status_code == 200:
                return None

        if response.status_code == 200:
            print(f"Successfully listed tasks for agent ID: {agent_id}.")
            return response_json
        else:
            print(f"Error listing tasks for agent {agent_id}. Status: {response.status_code}, Details: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to list tasks for agent {agent_id}: {e}")
        return None

def list_all_tasks_cli(api_key: str, base_url: str) -> list | None:
    """
    Lists all tasks for the authenticated user.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.

    Returns:
        A list of all tasks for the user (parsed JSON) on success (HTTP 200), None otherwise.
    """
    url = f"{base_url}tasks/browser-use/" # Note: different base path
    headers = {"X-Api-Key": api_key}

    print(f"--- Listing All Tasks for User ---")
    print(f"Request: GET {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        response_json = {}
        try:
            response_json = response.json()
            print(f"Response Body: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (Not JSON): {response.text}")
            if response.status_code == 200:
                return None

        if response.status_code == 200:
            print("Successfully listed all tasks for the user.")
            return response_json
        else:
            print(f"Error listing all tasks. Status: {response.status_code}, Details: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to list all tasks: {e}")
        return None

def update_task_cli(api_key: str, base_url: str, agent_id: str, task_id: str, new_prompt: str) -> dict | None:
    """
    Updates the input data of a specific task.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent the task belongs to.
        task_id: The ID of the task to update.
        new_prompt: The new input data for the task.

    Returns:
        The updated task details (parsed JSON) on success (HTTP 200), None otherwise.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/tasks/{task_id}/"
    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }
    payload = {"prompt": new_prompt}

    print(f"--- Updating Task ID: {task_id} (Agent ID: {agent_id}) ---")
    print(f"New Prompt: {new_prompt}")
    print(f"Request: PATCH {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")
    print(f"Body: {json.dumps(payload, indent=2)}")

    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        response_json = {}
        try:
            response_json = response.json()
            print(f"Response Body: {json.dumps(response_json, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (Not JSON): {response.text}")
            if response.status_code == 200:
                return None

        if response.status_code == 200:
            print(f"Task '{task_id}' updated successfully with new prompt.")
            return response_json
        else:
            print(f"Error updating task {task_id}. Status: {response.status_code}, Details: {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error making request to update task {task_id}: {e}")
        return None

def cancel_task_cli(api_key: str, base_url: str, agent_id: str, task_id: str) -> bool:
    """
    Cancels a specific task.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent the task belongs to.
        task_id: The ID of the task to cancel.

    Returns:
        True if the task was cancelled successfully (HTTP 200 or 204), False otherwise.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/tasks/{task_id}/cancel/"
    headers = {"X-Api-Key": api_key}

    print(f"--- Cancelling Task ID: {task_id} (Agent ID: {agent_id}) ---")
    print(f"Request: POST {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")

    try:
        response = requests.post(url, headers=headers, timeout=10) # No body for cancel typically
        print(f"Response Status Code: {response.status_code}")
        if response.text:
            try:
                response_json = response.json()
                print(f"Response Body: {json.dumps(response_json, indent=2)}")
            except json.JSONDecodeError:
                print(f"Response Body (Not JSON): {response.text}")
        else:
            print("Response Body: (No content)")

        if response.status_code == 200 or response.status_code == 204:
            print(f"Task '{task_id}' cancelled successfully.")
            return True
        else:
            print(f"Error cancelling task {task_id}. Status: {response.status_code}, Details: {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Error making request to cancel task {task_id}: {e}")
        return False

def delete_task_cli(api_key: str, base_url: str, agent_id: str, task_id: str) -> bool:
    """
    Soft deletes a specific task.

    Args:
        api_key: The API key for authentication.
        base_url: The base URL of the API.
        agent_id: The ID of the agent the task belongs to.
        task_id: The ID of the task to delete.

    Returns:
        True if the task was soft deleted successfully (HTTP 204), False otherwise.
    """
    url = f"{base_url}agents/browser-use/{agent_id}/tasks/{task_id}/"
    headers = {"X-Api-Key": api_key}

    print(f"--- Deleting Task ID: {task_id} (Agent ID: {agent_id}) ---")
    print(f"Request: DELETE {url}")
    print(f"Headers: {json.dumps(headers, indent=2)}")

    try:
        response = requests.delete(url, headers=headers, timeout=10)
        print(f"Response Status Code: {response.status_code}")
        if response.text:
            try:
                response_json = response.json()
                print(f"Response Body: {json.dumps(response_json, indent=2)}")
            except json.JSONDecodeError:
                print(f"Response Body (Not JSON): {response.text}")
        else:
            print("Response Body: (No content)")

        if response.status_code == 204:
            print(f"Task '{task_id}' soft deleted successfully.")
            return True
        else:
            print(f"Error soft deleting task {task_id}. Status: {response.status_code}, Details: {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Error making request to soft delete task {task_id}: {e}")
        return False


if __name__ == "__main__":
    api_key = os.environ.get(GOBII_API_KEY_ENV_VAR)
    if not api_key:
        print(f"Error: API key environment variable '{GOBII_API_KEY_ENV_VAR}' not found.", file=sys.stderr)
        print(f"Please set the {GOBII_API_KEY_ENV_VAR} environment variable.", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Gobii API Client CLI")
    parser.add_argument('--base-url', default=DEFAULT_BASE_URL,
                        help=f"Base URL for the Gobii API (default: {DEFAULT_BASE_URL})")

    resource_subparsers = parser.add_subparsers(dest='resource', title='Resources',
                                                help='Specify the resource type to manage', required=True)

    # Agent parser
    agent_parser = resource_subparsers.add_parser('agent', help='Manage agents')
    agent_actions = agent_parser.add_subparsers(dest='action', title='Agent Actions',
                                                help='Action to perform on an agent', required=True)

    # Agent create parser
    agent_create_parser = agent_actions.add_parser('create', help='Create a new agent')
    agent_create_parser.add_argument('--name', help='Name of the agent. If not provided, a random name will be generated.')

    # Agent delete parser
    agent_delete_parser = agent_actions.add_parser('delete', help='Delete an agent')
    agent_delete_parser.add_argument('--id', required=True, help='ID of the agent to delete (UUID)')

    # Agent list parser
    agent_list_parser = agent_actions.add_parser('list', help='List all agents')
    
    # Agent get parser
    agent_get_parser = agent_actions.add_parser('get', help='Get details of a specific agent')
    agent_get_parser.add_argument('--id', required=True, help='ID of the agent to retrieve (UUID)')

    # Agent update parser
    agent_update_parser = agent_actions.add_parser('update', help="Update an agent's name")
    agent_update_parser.add_argument('--id', required=True, help='ID of the agent to update (UUID)')
    agent_update_parser.add_argument('--name', required=True, help='New name for the agent')


    # Task parser
    task_parser = resource_subparsers.add_parser('task', help='Manage tasks')
    task_actions = task_parser.add_subparsers(dest='action', title='Task Actions', help='Action to perform on a task', required=True)

    # Task create parser
    task_create_parser = task_actions.add_parser('create', help='Create and assign a new task to an agent')
    task_create_parser.add_argument('--agent-id', required=True, help='ID of the agent to assign the task to (UUID)')
    task_create_parser.add_argument('--description', help='Description/input for the task. If not provided, a random description will be generated.')

    # Task get parser
    task_get_parser = task_actions.add_parser('get', help='Get the status/result of a specific task')
    task_get_parser.add_argument('--agent-id', required=True, help='ID of the agent the task belongs to (UUID)')
    task_get_parser.add_argument('--task-id', required=True, help='ID of the task to retrieve (UUID)')

    # Task list parser
    task_list_parser = task_actions.add_parser('list', help='List tasks for a specific agent')
    task_list_parser.add_argument('--agent-id', required=True, help='ID of the agent whose tasks to list (UUID)')

    # Task list-all parser
    task_list_all_parser = task_actions.add_parser('list-all', help='List all tasks for the authenticated user')
    # No specific arguments for list-all beyond global ones like base_url

    # Task update parser
    task_update_parser = task_actions.add_parser('update', help='Update the input data of a task')
    task_update_parser.add_argument('--agent-id', required=True, help='ID of the agent the task belongs to (UUID)')
    task_update_parser.add_argument('--task-id', required=True, help='ID of the task to update (UUID)')
    task_update_parser.add_argument('--description', required=True, help='New input data (description) for the task')

    # Task cancel parser
    task_cancel_parser = task_actions.add_parser('cancel', help='Cancel a specific task')
    task_cancel_parser.add_argument('--agent-id', required=True, help='ID of the agent the task belongs to (UUID)')
    task_cancel_parser.add_argument('--task-id', required=True, help='ID of the task to cancel (UUID)')

    # Task delete parser (soft delete)
    task_delete_parser = task_actions.add_parser('delete', help='Soft delete a specific task')
    task_delete_parser.add_argument('--agent-id', required=True, help='ID of the agent the task belongs to (UUID)')
    task_delete_parser.add_argument('--task-id', required=True, help='ID of the task to soft delete (UUID)')


    args = parser.parse_args()
    base_url_to_use = args.base_url

    if not base_url_to_use.endswith('/'):
        base_url_to_use += '/'

    if args.resource == 'agent':
        if args.action == 'create':
            agent_name = args.name
            if not agent_name:
                agent_name = generate_random_string("random-agent")
            print(f"Attempting to create agent with name: {agent_name} using base URL: {base_url_to_use}")
            created_agent_id = create_agent(api_key, base_url_to_use, agent_name)
            if created_agent_id:
                print(f"Agent creation successful. Agent ID: {created_agent_id}")
            else:
                print(f"Agent creation failed for name: {agent_name}")
        
        elif args.action == 'delete':
            agent_id_to_delete = args.id
            print(f"Attempting to delete agent with ID: {agent_id_to_delete} using base URL: {base_url_to_use}")
            deleted_successfully = delete_agent_cli(api_key, base_url_to_use, agent_id_to_delete)
            if deleted_successfully:
                print(f"Agent {agent_id_to_delete} deleted successfully.")
            else:
                print(f"Failed to delete agent {agent_id_to_delete}.")
        
        elif args.action == 'list':
            print(f"Attempting to list agents using base URL: {base_url_to_use}")
            agents_list = list_agents_cli(api_key, base_url_to_use)
            if agents_list is not None: # Could be an empty list which is a valid success
                print(f"Successfully listed agents. Count: {len(agents_list)}")
            else:
                print("Failed to list agents.")

        elif args.action == 'get':
            agent_id_to_get = args.id
            print(f"Attempting to get agent with ID: {agent_id_to_get} using base URL: {base_url_to_use}")
            agent_details = get_agent_cli(api_key, base_url_to_use, agent_id_to_get)
            if agent_details:
                print(f"Successfully retrieved agent {agent_id_to_get}.")
            else:
                print(f"Failed to retrieve agent {agent_id_to_get}.")

        elif args.action == 'update':
            agent_id_to_update = args.id
            new_agent_name = args.name
            print(f"Attempting to update agent ID: {agent_id_to_update} to name: '{new_agent_name}' using base URL: {base_url_to_use}")
            updated_agent = update_agent_name_cli(api_key, base_url_to_use, agent_id_to_update, new_agent_name)
            if updated_agent:
                print(f"Agent {agent_id_to_update} updated successfully.")
            else:
                print(f"Failed to update agent {agent_id_to_update}.")
        
        else:
            print(f"Unknown action '{args.action}' for resource 'agent'.")
            agent_parser.print_help()
            sys.exit(1)

    elif args.resource == 'task':
        if args.action == 'create':
            agent_id = args.agent_id
            task_description = args.description
            if not task_description:
                task_description = generate_random_string(f"random-task-for-{agent_id[:8]}")
            
            print(f"Attempting to assign task to agent ID: {agent_id} with description: '{task_description}' using base URL: {base_url_to_use}")
            task_details = assign_task(api_key, base_url_to_use, agent_id, task_description)
            if task_details:
                print(f"Task assigned successfully to agent {agent_id}. Task ID: {task_details.get('id')}")
            else:
                print(f"Failed to assign task to agent {agent_id}.")
        
        elif args.action == 'get':
            agent_id = args.agent_id
            task_id = args.task_id
            print(f"Attempting to get task details for Task ID: {task_id} (Agent ID: {agent_id}) using base URL: {base_url_to_use}")
            task_result = get_task_cli(api_key, base_url_to_use, agent_id, task_id)
            if task_result:
                print(f"Successfully processed 'get task' command for Task ID: {task_id}.")
            else:
                print(f"Failed to retrieve task details for Task ID: {task_id}.")

        elif args.action == 'list':
            agent_id = args.agent_id
            print(f"Attempting to list tasks for agent ID: {agent_id} using base URL: {base_url_to_use}")
            tasks_list = list_tasks_for_agent_cli(api_key, base_url_to_use, agent_id)
            if tasks_list is not None:
                print(f"Successfully listed tasks for agent {agent_id}. Count: {len(tasks_list)}")
            else:
                print(f"Failed to list tasks for agent {agent_id}.")
        
        elif args.action == 'list-all':
            print(f"Attempting to list all tasks for the user using base URL: {base_url_to_use}")
            all_tasks_list = list_all_tasks_cli(api_key, base_url_to_use)
            if all_tasks_list is not None:
                print(f"Successfully listed all tasks. Count: {len(all_tasks_list)}")
            else:
                print("Failed to list all tasks.")

        elif args.action == 'update':
            agent_id = args.agent_id
            task_id = args.task_id
            new_description = args.description # This is new_prompt
            print(f"Attempting to update task ID: {task_id} (Agent ID: {agent_id}) with new description: '{new_description}' using base URL: {base_url_to_use}")
            updated_task = update_task_cli(api_key, base_url_to_use, agent_id, task_id, new_description)
            if updated_task:
                print(f"Task {task_id} updated successfully.")
            else:
                print(f"Failed to update task {task_id}.")

        elif args.action == 'cancel':
            agent_id = args.agent_id
            task_id = args.task_id
            print(f"Attempting to cancel task ID: {task_id} (Agent ID: {agent_id}) using base URL: {base_url_to_use}")
            cancelled_successfully = cancel_task_cli(api_key, base_url_to_use, agent_id, task_id)
            if cancelled_successfully:
                print(f"Task {task_id} cancelled successfully.")
            else:
                print(f"Failed to cancel task {task_id}.")

        elif args.action == 'delete':
            agent_id = args.agent_id
            task_id = args.task_id
            print(f"Attempting to soft delete task ID: {task_id} (Agent ID: {agent_id}) using base URL: {base_url_to_use}")
            deleted_successfully = delete_task_cli(api_key, base_url_to_use, agent_id, task_id)
            if deleted_successfully:
                print(f"Task {task_id} soft deleted successfully.")
            else:
                print(f"Failed to soft delete task {task_id}.")
        
        else:
            print(f"Unknown action '{args.action}' for resource 'task'.")
            task_parser.print_help()
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)

    print("\nCLI command finished.")
