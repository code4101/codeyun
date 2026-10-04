import json
from pathlib import Path
import pytest
from backend.core.fanxiu.runtime_gui.sacred_exchange import sacred_exchange_source_point


def _tokens():
    return json.loads((Path(__file__).parent/'fixtures/fanxiu_sacred_source_112554_ocr.json').read_text(encoding='utf-8'))


WINDOW=dict(x=86,y=600,w=740,h=800)


def test_real_failure_viewport_has_no_click_for_source_below_fold():
    assert sacred_exchange_source_point(_tokens(),window=WINDOW,name="灵眼神树",quantity=32914) is None


def test_real_split_title_and_counter_bind_visible_row_independent_of_bag_order():
    assert sacred_exchange_source_point(_tokens(),window=WINDOW,name="蟠桃仙树",quantity=992376)==(456.0,684.5)


@pytest.mark.parametrize("quantity",[32914,992375,319084])
def test_wrong_or_other_row_counter_cannot_authorize_source(quantity):
    assert sacred_exchange_source_point(_tokens(),window=WINDOW,name="蟠桃仙树",quantity=quantity) is None


def test_clipped_counter_cannot_authorize_a_click():
    assert sacred_exchange_source_point(_tokens(),window=dict(WINDOW,h=120),name="蟠桃仙树",quantity=992376) is None


def test_scroll_translation_preserves_binding_without_fixed_row_offset():
    shifted=[dict(t,y=t['y']-13) for t in _tokens()]
    assert sacred_exchange_source_point(shifted,window=WINDOW,name="蟠桃仙树",quantity=992376)==(456.0,671.5)


def test_duplicate_named_source_and_counter_fail_closed():
    tokens=_tokens(); duplicate=[dict(t,y=t['y']+800,parent_line_id='copy-'+t['parent_line_id']) for t in tokens if 620<t['y']<740]
    with pytest.raises(RuntimeError,match="not unique"):
        sacred_exchange_source_point(tokens+duplicate,window=dict(WINDOW,h=1600),name="蟠桃仙树",quantity=992376)

def test_source_navigation_selectors_match_actual_632_asset_contract():
    import ast
    from backend.core.fanxiu.data_annotation.tasks import sacred_exchange_supply
    fixture=json.loads((Path(__file__).parent/'fixtures/fanxiu_sacred_source_632_window.json').read_text(encoding='utf-8'))
    tree=ast.parse(Path(sacred_exchange_supply.__file__).read_text(encoding='utf-8'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_open_source_shop')
    selectors=[n.args[1].value for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('shape_box','drag_shape_content')]
    assert selectors==[fixture['title'],fixture['title']]
    assert sacred_exchange_source_point(_tokens(),window=fixture['window'],name="蟠桃仙树",quantity=992376)==(452.25,684.5)
    assert sacred_exchange_source_point(_tokens(),window=fixture['window'],name="灵眼神树",quantity=32914) is None

from types import SimpleNamespace
from backend.core.fanxiu.data_annotation.tasks import sacred_exchange_supply
from backend.core.fanxiu.data_annotation.tasks.sacred_exchange_supply import require_sacred_scene
@pytest.mark.parametrize('scene',[587,632,913])
def test_wrong_scene_rejected_before_shop_read(scene):
 with pytest.raises(RuntimeError,match=f'actual #{scene}'):
  require_sacred_scene(SimpleNamespace(id=scene),(633,))

def test_shop_transition_returns_only_after_exact_scene():
 assert require_sacred_scene(SimpleNamespace(id=633),(633,))==633

@pytest.mark.parametrize('scene',[587,633])
def test_source_navigation_modal_guard(scene):
 class Context:
  def cur_frame(self,**kwargs): return 'frame'
  def full_frame_ocr_tokens(self,**kwargs): return []
  def shape_box(self,*args): return {}
  def click_frame_point(self,*args): self.clicked=args
  def wait_scene(self,*args,**kwargs):
   yield 'wait'
   return SimpleNamespace(id=scene)
 c=Context(); ns=dict(vars(sacred_exchange_supply))
 ns['runtime_backpack_total']=lambda *a:33122
 ns['sacred_exchange_source_point']=lambda *a,**k:(452.25,1291)
 fn=type(sacred_exchange_supply._open_source_shop)(sacred_exchange_supply._open_source_shop.__code__,ns)
 gen=fn(c,{},SimpleNamespace(source_item_id=4000005,source_item_name='灵眼神树',label='supply'))
 assert next(gen)=='wait'
 if scene==587:
  with pytest.raises(RuntimeError,match='actual #587'): next(gen)
 else:
  with pytest.raises(StopIteration): next(gen)
 assert c.clicked==(632,452.25,1291)
