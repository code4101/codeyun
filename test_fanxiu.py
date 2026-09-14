import json
from datetime import datetime
from backend.core.fanxiu.data_annotation import kernel_scheduler_control
from backend.core.temp_paths import codeyun_temp_root

state_path = codeyun_temp_root("fanxiu/kernel_scheduler_state.json")
facts_path = codeyun_temp_root("fanxiu/world_facts.json")
exec_path = codeyun_temp_root("fanxiu/kernel_execution_state.json")

try:
    tasks = kernel_scheduler_control.read_scheduler_tasks(scheduler_state_path=state_path, world_facts_path=facts_path, now=datetime.now())
    status = kernel_scheduler_control.read_kernel_scheduler_status(exec_path)

    print("Status:")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    print("Tasks:")
    for t in tasks:
        print(f"- {t.get('id')}: next_time={t.get('next_time')} error={t.get('last_error')}")
except Exception as e:
    import traceback
    traceback.print_exc()