import sys

from backend.core.device import get_device_id
from backend.core.settings import get_settings

settings = get_settings()

def main():
    print("--- CodeYun Machine Token Retriever ---")

    device_id = get_device_id()
    token = settings.device_token

    if not device_id:
        print("Error: device_id not found. Please run the backend to generate it.")
        sys.exit(1)

    print(f"Device ID: {device_id}")

    if token:
        print(f"\nMachine Token: {token}")
    else:
        print("\nNo token found for this device.")

if __name__ == "__main__":
    main()
