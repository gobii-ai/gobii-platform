#!/usr/bin/env python3
"""
Example script demonstrating secrets functionality.
This is for local testing only - does not make actual API calls.
"""
import os
import json
import requests

# Configuration
BASE_URL = "http://127.0.0.1:8000/api/v1/"
API_KEY = os.getenv("GOBII_API_KEY", "your-api-key-here")

def test_secrets_api():
    """Test the secrets functionality with the local API."""
    headers = {
        "X-Api-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    # Test case 1: Basic secrets usage
    print("=== Test 1: Basic Secrets ===")
    data = {
        "prompt": "Login to the admin panel using x_username and x_password",
        "secrets": {
            "x_username": "admin@example.com",
            "x_password": "secretPassword123"
        }
    }
    
    response = requests.post(f"{BASE_URL}tasks/browser-use/", 
                           headers=headers, 
                           json=data)
    
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
    if response.status_code == 201:
        task_id = response.json()['id']
        print(f"Task created successfully with ID: {task_id}")
        
        # Check that secrets are not in the response
        if 'secrets' not in response.json():
            print("✅ Secrets correctly excluded from response")
        else:
            print("❌ Secrets leaked in response!")
    
    print("\n" + "="*50 + "\n")
    
    # Test case 2: Invalid secret keys
    print("=== Test 2: Invalid Secret Keys ===")
    invalid_cases = [
        {"1invalid": "value"},  # starts with number
        {"invalid-key": "value"},  # contains dash
        {"invalid key": "value"},  # contains space
    ]
    
    for i, secrets in enumerate(invalid_cases, 1):
        print(f"Testing invalid case {i}: {list(secrets.keys())[0]}")
        data = {
            "prompt": "Test prompt",
            "secrets": secrets
        }
        
        response = requests.post(f"{BASE_URL}tasks/browser-use/", 
                               headers=headers, 
                               json=data)
        
        if response.status_code == 400:
            print(f"✅ Correctly rejected: {response.json()}")
        else:
            print(f"❌ Should have been rejected: {response.status_code}")
    
    print("\n" + "="*50 + "\n")
    
    # Test case 3: Secrets with structured output
    print("=== Test 3: Secrets + Structured Output ===")
    data = {
        "prompt": "Login using x_username and x_password, then get account balance",
        "secrets": {
            "x_username": "user@example.com",
            "x_password": "userpass123"
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "balance": {"type": "string"},
                "currency": {"type": "string"}
            },
            "required": ["balance", "currency"]
        }
    }
    
    response = requests.post(f"{BASE_URL}tasks/browser-use/", 
                           headers=headers, 
                           json=data)
    
    print(f"Status: {response.status_code}")
    if response.status_code == 201:
        print("✅ Task with secrets + schema created successfully")
        print(f"Task ID: {response.json()['id']}")
    else:
        print(f"❌ Failed: {response.json()}")

if __name__ == "__main__":
    print("Testing Gobii Secrets API")
    print("=" * 50)
    
    if API_KEY == "your-api-key-here":
        print("❌ Please set GOBII_API_KEY environment variable")
        print("Example: export GOBII_API_KEY='your-actual-key'")
        exit(1)
    
    try:
        test_secrets_api()
        print("✅ All tests completed!")
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to local server")
        print("Make sure the Django development server is running:")
        print("python manage.py runserver")
    except Exception as e:
        print(f"❌ Error: {e}") 