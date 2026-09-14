import json
from backend.core.fanxiu.data_annotation import kernel_scheduler_control
from backend.core.temp_paths import codeyun_temp_root

try:
    status = kernel_scheduler_control.take_ai_control(
        entry_id="",
        interrupt_any_cell=True,
        execution_state_path=codeyun_temp_root("fanxiu/kernel_execution_state.json"),
        scheduler_settings_path=codeyun_temp_root("fanxiu/kernel_scheduler_settings.json")
    )
    print("AI control taken.")
    print(json.dumps(status, ensure_ascii=False, indent=2))
except Exception as e:
    import traceback
    traceback.print_exc()