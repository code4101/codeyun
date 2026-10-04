import threading
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju_tail as module

class Runtime:
    def __init__(self, landings):
        self.landings = iter(landings)
        self.clicks = []
        self.waits = []
    def wait_scene(self, scenes, **kwargs):
        self.waits.append(scenes)
        if False:
            yield
        return next(self.landings)
    def wait_click(self, scene, shape, **kwargs):
        self.clicks.append((scene, shape))
        if False:
            yield

class ShopReached(Exception):
    pass

@pytest.mark.parametrize('settled,start,landings,reached,clicks', [
    (True,'home',[677],False,0),
    (True,'home',[682,682],False,1),
    (True,'shop',[682],False,0),
    (True,'home',[SimpleNamespace(id=682),SimpleNamespace(id=911)],True,1),
    (False,'home',[677,911],True,1),
    (True,'shop',[911],True,0),
])
def test_entry_requires_actual_exchange_page(monkeypatch,settled,start,landings,reached,clicks):
    end = datetime(2026,10,3,22,tzinfo=timezone(timedelta(hours=8)))
    occurrence = SimpleNamespace(end_at=end)
    monkeypatch.setattr(module,'job_now',lambda tz:end+timedelta(seconds=1 if settled else -1))
    monkeypatch.setattr(module,'_load_persisted_detail',lambda _: (SimpleNamespace(id='activity-1'),{}))
    monkeypatch.setattr(module,'plan_exchange_tail_purchases',lambda *args,**kwargs:([object()],set(),{}))
    reads=[]
    def refresh(activity_id):
        reads.append(activity_id)
        raise ShopReached()
    monkeypatch.setattr(module,'_refresh_persisted_detail',refresh)
    runtime=Runtime(landings)
    generator=module.execute_tiandi_yiju_exchange_tail(None,{},occurrence=occurrence,stop_event=threading.Event(),context=runtime,start=start)
    with pytest.raises(ShopReached if reached else RuntimeError):
        while True:
            next(generator)
    assert reads == (['activity-1'] if reached else [])
    assert len(runtime.clicks)==clicks
    if clicks:
        assert runtime.clicks[0][0] == (682 if settled else 677)

class ProductRuntime:
    def __init__(self, reveal=True, ambiguous=False):
        self.reveal=reveal
        self.ambiguous=ambiguous
        self.drags=[]
        self.clicks=[]
    def view(self, scene):
        def shape(name):
            top=300 if name=='商品列表' else 300+(int(name[-1])-1)*180
            return SimpleNamespace(box=lambda:{'x':50,'y':top,'w':800,'h':1000 if name=='商品列表' else 160})
        return SimpleNamespace(get_shape=shape)
    def full_frame_ocr_tokens(self, **kwargs):
        if not self.drags and not self.ambiguous or not self.reveal:
            return []
        rows=[{'parent_line_id':'name','text':'装备玄铁宝匣','x':210,'y':330,'w':180,'h':30},
              {'parent_line_id':'price','text':'100','x':220,'y':385,'w':40,'h':25}]
        if self.ambiguous:
            rows += [dict(r,parent_line_id=r['parent_line_id']+'2',y=r['y']+180) for r in rows]
        return rows
    def drag_frame_point(self,*args,**kwargs):
        self.drags.append(args)
    def wait_action_settle(self,*args):
        if False:
            yield
    def click_frame_point(self,*args):
        self.clicks.append(args)

@pytest.mark.parametrize('reveal,ambiguous,success,drags',[(True,False,True,1),(False,False,False,2),(True,True,False,0)])
def test_clipped_product_scroll_is_bounded_and_exact(reveal,ambiguous,success,drags):
    runtime=ProductRuntime(reveal,ambiguous)
    generator=module._click_planned_product(runtime,name='装备玄铁宝匣',unit_price=100)
    if success:
        list(generator)
    else:
        with pytest.raises(RuntimeError):
            list(generator)
    assert len(runtime.drags)==drags
    assert len(runtime.clicks)==int(success)

def test_visible_wrong_price_is_never_clicked():
    runtime=ProductRuntime()
    with pytest.raises(RuntimeError):
        list(module._click_planned_product(runtime,name='装备玄铁宝匣',unit_price=101))
    assert len(runtime.drags)==2
    assert runtime.clicks==[]
