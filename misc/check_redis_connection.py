import sys
import os
import django

# Add the project directory to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from config.redis_client import get_redis_client, REDIS_URL

def check_redis():
    try:
        # Get Redis client
        r = get_redis_client()
        
        # Perform PING command
        if r.ping():
            print(f"Successfully connected to Redis at {REDIS_URL} and PING was successful.")
        else:
            # This case is unlikely with r.ping() as it usually raises an exception on failure
            # or returns True on success. However, kept for logical completeness as per pseudocode.
            print(f"Connected to Redis at {REDIS_URL}, but PING failed.")
    except Exception as e:
        print(f"Failed to connect to Redis at {REDIS_URL}. Error: {e}")

if __name__ == "__main__":
    check_redis()
