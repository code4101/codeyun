import json
import os
import sys

# Define paths relative to this script: backend/scripts/get_machine_token.py
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPTS_DIR)
DATA_DIR = os.path.join(BACKEND_DIR, "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

def get_local_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: Config file not found at {CONFIG_FILE}")
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading config file: {e}")
        return {}

def main():
    print("--- CodeYun Machine Token Retriever ---")
    
    config = get_local_config()
    
    device_id = config.get("system_id")
    token = config.get("api_token")
    
    if not device_id:
        # Fallback for old system? Or just error.
        print("Error: 'system_id' not found in config.json. Please run the backend to generate it.")
        sys.exit(1)
        
    print(f"Device ID: {device_id}")
    
    if token:
        print(f"\nMachine Token: {token}")
    else:
        print("\nNo token found for this device.")

if __name__ == "__main__":
    main()