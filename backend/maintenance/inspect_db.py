import sqlite3
import os
import json

db_path = r'C:\home\chenkunze\data\m2603codeyun\codepc_mi15\codeyun.db'

print(f"Inspecting DB: {db_path}")

try:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Check Tasks
    print("\n--- Tasks ---")
    cursor.execute("SELECT id, name, device_id, command FROM task")
    tasks = cursor.fetchall()
    for t in tasks:
        print(f"ID: {t['id']}, Name: {t['name']}, DeviceID: {t['device_id']}, Cmd: {t['command'][:50]}...")
        
    # 2. Check Device Entries
    print("\n--- User Device Entries ---")
    try:
        cursor.execute("SELECT device_id, name, mode, url FROM userdeviceentry")
        devices = cursor.fetchall()
        for d in devices:
            print(f"DeviceID: {d['device_id']}, Name: {d['name']}, Mode: {d['mode']}, URL: {d['url']}")
    except Exception as e:
        print(f"Error querying userdeviceentry: {e}")

    # 3. Check System ID / Identity if possible (often in files, but maybe in DB too?)
    # The app uses a file for identity usually.
    
    conn.close()

except Exception as e:
    print(f"Error: {e}")

# 4. Check identity file
identity_path = os.path.join(os.path.dirname(db_path), "device_identity.json")
if os.path.exists(identity_path):
    print(f"\n--- Identity File ({identity_path}) ---")
    try:
        with open(identity_path, 'r') as f:
            print(json.load(f))
    except:
        print("Error reading identity file")
else:
    print(f"\nIdentity file not found at {identity_path}")

# Check config.json as well
config_path = os.path.join(os.path.dirname(db_path), "config.json")
if os.path.exists(config_path):
    print(f"\n--- Config File ({config_path}) ---")
    try:
        with open(config_path, 'r') as f:
            print(json.load(f))
    except:
        print("Error reading config file")
