"""资源榜的单期资质达档入口，复用实机验收过的灵兽使用事务。"""
from datetime import datetime


def run_lingchong_resource_use_flow(context, *, occurrence, options):
    """Only the authorized exact occurrence may spend inside its real window.

    Planning clocks never authorize a game action. Before opening, this returns
    a pending receipt and performs no navigation, swallowing, or pill use.
    Highest tier and progress always come from the loaded QuestMgr instance.
    """
    now = datetime.now().astimezone()
    if occurrence.instance_key not in options.get('instance_keys', []):
        raise ValueError('本期灵宠资源使用没有明确授权')
    if now < occurrence.start_at:
        return {'status': 'pending', 'retry_at': occurrence.start_at.isoformat(),
                'performed_actions': False, 'message': '灵宠竞武真实窗口未到'}
    if now >= occurrence.end_at:
        raise RuntimeError('灵宠竞武真实窗口已结束，禁止补消费')
    from .pet_resource_use import initialize_pet_resources, read_pet_resource_progress
    progress = read_pet_resource_progress(occurrence.activity_id)
    if progress['complete']:
        from .lingchong_task_rewards import claim_lingchong_preliminary_rewards
        rewards = yield from claim_lingchong_preliminary_rewards(context, activity_id=occurrence.activity_id)
        return {'status': 'completed', 'progress': progress, 'performed_actions': False, 'task_rewards': rewards}
    # This is the already taught first-growing-pet adapter. Other choices need
    # their own real GUI binding; never pick a pet from model ID ordering.
    if options.get('pet_id') != 7101 or options.get('pet_name') != '玄霜冰凤':
        raise ValueError('灵宠达档目标尚未绑定正式界面')
    from .pet_aptitude_navigation import prepare_pet_resource_page, enter_first_growing_pet_aptitude
    yield from context.go_scene(483)
    yield from prepare_pet_resource_page(context)
    frame = context.cur_frame(update=True)
    rows = context.ocr_fragments_in_shapes(483, ['第一个成长灵兽'], frame_data_url=frame)
    if not any(options['pet_name'] in row.get('text', '') for row in rows):
        raise RuntimeError('首个成长灵兽不是已绑定玄霜冰凤，禁止消费')
    yield from enter_first_growing_pet_aptitude(context)
    result = yield from initialize_pet_resources(context,
        activity_id=occurrence.activity_id, pet_id=options['pet_id'],
        start_date=occurrence.start_at.date().isoformat(),
        end_date=occurrence.end_at.date().isoformat(), max_batches=300,
        consumption_window=(occurrence.start_at, occurrence.end_at),
        scheduler_task_id='resource-ranking')
    yield from context.go_scene(34)
    if result['status'] == 'pending':
        return {**result, 'performed_actions': bool(result['receipts']),
                'message': '灵宠已核实批次保存；安全离场让位更高级作业，正式 checkpoint 待续'}
    if result['status'] != 'complete':
        raise RuntimeError(f'灵宠达档仍有资源/容量义务，需群内确认：{result}')
    from .lingchong_task_rewards import claim_lingchong_preliminary_rewards
    rewards = yield from claim_lingchong_preliminary_rewards(context, activity_id=occurrence.activity_id)
    return {**result, 'status': 'completed', 'performed_actions': bool(result['receipts']), 'task_rewards': rewards}
