from backend.core.fanxiu.remote.sessions import get_fanxiu_kernel_scheduler_status
import json

try:
    status = get_fanxiu_kernel_scheduler_status()
    print(json.dumps(status, default=str, indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error: {e}")