"""将既有预备→突破入口适配为每日资源组件；不定义新的培养策略。

错升重置、初始补给仍由各自已有 API 负责；本组件的完成只代表预备阶段
本轮处理结束，不代表全馆从错升到突破全部完成。
"""
from backend.core.temp_paths import codeyun_temp_root
from ...instrumentation.spirit_artifact_collector import collect_spirit_artifact_snapshot_once
from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from .spirit_artifact_cultivation import run_spirit_artifact_prepared_round

STAGE_ID = 'spirit-artifact-prepared-update'
STAGE_VERSION = '1'
STAGE_LABEL = '洗灵预备到突破'


def update_prepared_spirit_artifacts(context, execute, *, moment, stop_at):
    """基础更新后调用原培养 API；准入、排序、消耗和突破判据均由其负责。

    沿用原入口默认消耗预算，未完成不签每日凭证。期限由父作业传入；
    这里仅补充当前进程、证据路径和成功后的正式返回路径。
"""
    hall = collect_spirit_artifact_snapshot_once()
    observation = hall.get('runtime_debug', {})
    if not hall.get('runtime_complete') or not all(
            observation.get(k) for k in ('pid', 'process_start_ticks')):
        raise RuntimeError('预备培养缺少完整当前全馆与进程事实')
    result = run_spirit_artifact_prepared_round(
        context, execute,
        process_identity=(observation['pid'], observation['process_start_ticks']),
        evidence_dir=codeyun_temp_root('spirit-artifact', 'daily-prepared', moment.date().isoformat()),
        stop_at=stop_at,
    )
    if result['status'] != 'complete':
        raise RuntimeError(f'洗灵预备到突破未完成：{result}')
    SpiritArtifactCleanseRuntimeGuiAdapter(context, execute).return_to_world()
    return {'result': 'success', 'outcome': 'complete', 'prepared_round': result}
