import json
from backend.api.fanxiu import _kernel_scheduler_status

try:
    status = _kernel_scheduler_status(include_cell_logs=True)
    print("Status:")
    print(json.dumps(status, ensure_ascii=False, indent=2))
except Exception as e:
    import traceback
    traceback.print_exc()