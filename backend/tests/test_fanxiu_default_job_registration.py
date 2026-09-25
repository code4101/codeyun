"""任务注册表的初始化与重载契约；独立进程中不执行任何任务。"""
import subprocess
import sys


def test_default_registration_is_idempotent_and_recovers_after_registry_reload():
    code = """
import importlib
from backend.core.fanxiu.data_annotation import jobs, default_jobs

def definitions():
    return {d.task_type: d for d in jobs.list_fanxiu_data_annotation_task_cell_definitions()}

default_jobs.register_fanxiu_default_jobs()
first = definitions()
assert first
# 正常初始化保留当前 handler，包括 Kernel 中仍持有的引用。
default_jobs.register_fanxiu_default_jobs()
assert all(definitions()[key].handler is value.handler for key, value in first.items())
# 显式刷新替换定义，但无需运行 Cell 或构造游戏状态。
default_jobs.register_fanxiu_default_jobs(force=True)
second = definitions()
assert second.keys() == first.keys()
assert all(second[key].handler is not value.handler for key, value in first.items())
# 注册表重载后，已缓存的默认模块仍能重建完整目录。
importlib.reload(jobs)
assert definitions() == {}
default_jobs.register_fanxiu_default_jobs()
assert definitions().keys() == first.keys()
# 默认实现重载时重新执行声明，不沿用旧版本的完整性清单。
before_reload = definitions()
importlib.reload(default_jobs)
default_jobs.register_fanxiu_default_jobs()
assert definitions().keys() == first.keys()
assert all(definitions()[key].handler is not value.handler for key, value in before_reload.items())
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_registry_payloads_are_isolated_at_registration_and_query_boundaries():
    code = """
from backend.core.fanxiu.data_annotation import jobs
payload = {"items": [{"count": 2}]}
def handler(*args):
    raise AssertionError("查询不得执行任务")
jobs.register_fanxiu_data_annotation_task_cell(
    "snapshot_probe", "probe", standard_job_payload=payload
)(handler)
payload["items"][0]["count"] = 9
first = jobs.get_fanxiu_data_annotation_task_cell_definition("snapshot_probe")
assert first.standard_job_payload == {"items": [{"count": 2}]}
first.standard_job_payload["items"][0]["count"] = 7
listed = next(d for d in jobs.list_fanxiu_data_annotation_task_cell_definitions()
              if d.task_type == "snapshot_probe")
assert listed.standard_job_payload == {"items": [{"count": 2}]}
listed.standard_job_payload["items"].append({"count": 5})
last = jobs.get_fanxiu_data_annotation_task_cell_definition("snapshot_probe")
assert last.standard_job_payload == {"items": [{"count": 2}]}
assert first.handler is listed.handler is last.handler is handler
assert jobs.get_fanxiu_data_annotation_task_cell_definition("absent") is None
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
