"""凌霄寻宝本期礼包商店：免费/灵石白名单和服务器计数闭环，不创建 Job。"""
import re
import time
from backend.core.fanxiu.instrumentation.activity_template_store import (
    read_activity_template_store, read_revenue_activity_round_config,
)
from backend.core.fanxiu.instrumentation.activity_gift import read_activity_gift_runtime_snapshot
from backend.core.fanxiu.instrumentation.activity_runtime import read_activity_period_runtime_snapshot
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.runtime_gui.activity_template_store import activity_gift_button

ACTIVITY_ID, TURN_ID = 30000008, 30000009
PERIOD = (1790524805000, 1791388790000)  # 本期：2026-09-28 00:00:05～10-07 23:59:50
SHOP, MAIN, POSTER = 919, 918, 917


def activity_rounds(open_grade, start_ms, end_ms):
    """按客户端 openGrade 的开始日计算各轮有效区间；不虚构不存在的轮次。"""
    rows = []
    for part in open_grade.split('|'):
        match = re.fullmatch(r'(\d+)_(\d+)_(\d+)', part)
        if not match:
            raise ValueError('服务器轮次配置格式未知')
        start, end, grade = map(int, match.groups())
        if not 1 <= start <= end or grade <= 0:
            raise ValueError('服务器轮次配置无效')
        rows.append(dict(round=grade, start_ms=start_ms+(start-1)*86400000,
                         end_ms=min(end_ms,start_ms+end*86400000)))
    if len({r['round'] for r in rows}) != len(rows) or rows != sorted(rows,key=lambda r:r['start_ms']):
        raise ValueError('服务器轮次重复或顺序不一致')
    for index, row in enumerate(rows[:-1]):
        row['end_ms'] = min(row['end_ms'],rows[index+1]['start_ms'])
    if any(r['start_ms'] >= r['end_ms'] for r in rows):
        raise ValueError('服务器轮次超出本期范围')
    return rows


def allowed_store_rows(snapshot):
    """只授权没有现金 payId 且无消耗/消耗 Item|1 的礼包；其它币种不操作。"""
    if snapshot.get('complete') is not True:
        raise RuntimeError(f"礼包计数不完整：{snapshot.get('reason')}")
    result = []
    for row in snapshot['items']:
        if row['activity_id'] != ACTIVITY_ID:
            raise RuntimeError('礼包活动身份不一致')
        if row['pay_id']:
            continue
        cost = row['costs']
        match = re.fullmatch(r'Item\|1_([1-9]\d*)', cost) if cost else None
        if cost and match is None:
            continue
        if row['gift_type'] != 'GRADE_CHANGE' or any(row[k] for k in
                ('show_condition','hide_condition','buy_condition')):
            raise RuntimeError('本期礼包门槛变化，保留现场')
        result.append({**row, 'unit_price':int(match[1]) if match else 0})
    if not result or len({r['id'] for r in result}) != len(result):
        raise RuntimeError('授权礼包目录缺失或重复')
    return result


def read_lingxiao_store_monitor():
    """本期未来义务的高层只读 API；无需游戏页面、Cell 或调度控制权。"""
    now = time.time()*1000
    if now >= PERIOD[1]:
        return dict(complete=True,phase='closed',period=PERIOD)
    period = read_activity_period_runtime_snapshot(TURN_ID)
    actual = (period['start_time_ms'],period['end_time_ms'])
    if actual != PERIOD:
        return dict(complete=True,phase='instance_changed',period=PERIOD,actual_period=actual)
    cfg = read_revenue_activity_round_config(TURN_ID)
    rounds = activity_rounds(cfg['open_grade'],*PERIOD)
    third = next((r for r in rounds if r['round']==3),None)
    phase = ('no_third' if third is None else
             'third_open' if third['start_ms'] <= now < third['end_ms'] else
             'third_waiting' if now < third['start_ms'] else 'third_closed')
    return dict(complete=True,phase=phase,period=PERIOD,rounds=rounds,third=third)


def _check_instance(store, expected_round):
    if store['activity_id'] != ACTIVITY_ID or store['turn_activity_id'] != TURN_ID:
        raise RuntimeError('凌霄商店实例身份不符')
    period = read_activity_period_runtime_snapshot(TURN_ID)
    if (period['start_time_ms'],period['end_time_ms']) != PERIOD:
        raise RuntimeError('本期实例已变化；未授权跨期购买')
    rounds = activity_rounds(store['open_grade'],*PERIOD)
    active = [r for r in rounds if r['start_ms'] <= time.time()*1000 < r['end_ms']]
    if len(active) != 1 or active[0]['round'] != expected_round:
        raise RuntimeError(f'当前不是已授权第 {expected_round} 轮')
    return rounds


def _ready(context):
    match = yield from context.wait_scene([SHOP, MAIN, POSTER, 34],wait=5,required=False)
    scene = match.scene_id if match else None
    if scene not in (SHOP, MAIN, POSTER):
        yield from context.go_scene(34)
        yield from context.wait_click_ocr_text(34,'凌霄寻宝',in_shapes=['左侧菜单'],max_scrolls_per_direction=0)
        yield from context.wait_scene_exact([POSTER],timeout=30)
        scene = POSTER
    if scene == POSTER:
        yield from context.wait_click_then_scene(POSTER,'仙晶秘宝',MAIN,timeout=40,max_clicks=1)
        scene = MAIN
    if scene == MAIN:
        yield from context.wait_click_then_scene(MAIN,'活动商店',SHOP,timeout=25,max_clicks=1)


def _button(context, title):
    viewport = context.shape(SHOP,'礼包列表').box()
    title_template = context.shape(SHOP,'首礼包标题').box()
    button_template = context.shape(SHOP,'首礼包购买').box()
    for direction in ('left','right'):
        for _ in range(12):
            yield from context.wait_scene_exact([SHOP],timeout=30)
            try:
                return activity_gift_button(context.full_frame_ocr_tokens(context.cur_frame(update=True)),
                    title=title, viewport=viewport,title_template=title_template,button_template=button_template)
            except ValueError:
                changed = yield from context.scroll_shape_content(SHOP,'礼包列表',direction=direction)
                if not changed:
                    break
    raise RuntimeError(f'礼包 {title} 未唯一定位，保留现场')


def purchase_lingxiao_store_round(context, *, expected_round=2, log=print):
    """整轮幂等；重入只读本期当前剩余次数，单次点击后计数 +1 才继续。

    直接购买按钮经原生 GiftItem 证明一次买一份，免费/灵石分支 payId=0；
    其它现金、代币及充值卡分支全部排除。无反馈或异常计数禁止重发。
    服务器本期 openGrade=1_10_1|6_10_2，只有两轮；第三轮只能在同一
    PERIOD 下服务器新增后才授权执行，不能把下一期称作本期第三轮。
    """
    yield from _ready(context)
    store = read_activity_template_store()
    rounds = _check_instance(store,expected_round)
    initial = read_activity_gift_runtime_snapshot([ACTIVITY_ID])
    rows = allowed_store_rows(initial)
    balance = read_wallet_currency_snapshot(1)['exchange_currency']
    total = sum(r['remaining_times']*r['unit_price'] for r in rows)
    if balance < total:
        raise RuntimeError('灵石不足以完成本轮，保留现场，不采购补给')
    purchases = 0
    snapshot = initial
    for target in rows:
        while True:
            current = next(r for r in allowed_store_rows(snapshot) if r['id']==target['id'])
            if not current['remaining_times']:
                break
            point = yield from _button(context,current['title'])
            guard = read_activity_template_store()
            _check_instance(guard,expected_round)
            binding = [r for r in guard['gift_bindings'] if r['id']==current['id']]
            if (len(binding)!=1 or binding[0]['can_buy'] is not True or
                    binding[0]['enough'] is not True or binding[0]['sold_out'] is not False):
                raise RuntimeError('当前礼包身份或原生购买门槛不完整，禁止点击')
            if any(binding[0][k] != current[k] for k in ('activity_id','pay_id','costs')):
                raise RuntimeError('已渲染礼包币种/价格与新鲜服务器目录不一致，禁止点击')
            context.click_frame_point(SHOP,*point)
            yield from context.wait_action_settle(1)
            yield from context.wait_scene_exact([SHOP],timeout=40,observation_scenes=[177,578],
                label='凌霄礼包：等待奖励返回商店')
            after = read_activity_gift_runtime_snapshot([ACTIVITY_ID])
            after_rows = allowed_store_rows(after)
            updated = next(r for r in after_rows if r['id']==current['id'])
            if updated['purchased_times'] != current['purchased_times']+1:
                raise RuntimeError('礼包购买计数未恰好 +1，禁止重复提交')
            before_counts = {r['id']:r['purchased_times'] for r in snapshot['items']}
            if any(r['purchased_times'] != before_counts[r['id']] for r in after['items'] if r['id']!=current['id']):
                raise RuntimeError('非目标礼包计数变化，保留现场')
            snapshot = after
            purchases += 1
            log(f"凌霄第{expected_round}轮/{current['title']}：{updated['purchased_times']}/{updated['limit_times']}")
    final = allowed_store_rows(snapshot)
    if any(r['remaining_times'] for r in final):
        raise RuntimeError('本期授权礼包尚未全部售罄')
    after_balance = read_wallet_currency_snapshot(1)['exchange_currency']
    if balance-after_balance != total:
        raise RuntimeError(f'灵石扣减与本轮购买不一致：实际 {balance-after_balance}，预期 {total}')
    yield from context.wait_click(SHOP,'返回',timeout=20)
    yield from context.wait_scene_exact([34],timeout=25)
    return dict(result='success',outcome='complete',round=expected_round,purchases=purchases,
        spirit_stone_spent=total,spirit_stone_before=balance,spirit_stone_after=after_balance,
        items=[{k:r[k] for k in ('id','title','purchased_times','limit_times','remaining_times')} for r in final],
        server_rounds=rounds,final_scene=34)
