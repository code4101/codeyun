
import sys
import os

# Ensure codeyun root is in path, just like dev.py does
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, root_dir)

os.environ.setdefault("CODEYUN_ENV", "test")
os.environ.setdefault("CODEYUN_LOAD_DOTENV", "0")

try:
    from backend.app import app
    print("Successfully imported backend.app")
except ImportError as e:
    print(f"Failed to import backend.app: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Error during import: {e}")
    sys.exit(1)
