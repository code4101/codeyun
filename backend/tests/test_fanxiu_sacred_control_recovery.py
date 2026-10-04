import json
from pathlib import Path
import pytest
from backend.core.fanxiu.runtime_gui import common_shop_quantity as quantity
from backend.core.fanxiu.runtime_gui.sacred_exchange import sacred_shop_product_point

@pytest.mark.parametrize('observed',[1,164,165])
def test_maximum_track_requires_drag_and_exact_native_proof(observed):
 calls=[]
 class RecordingContext:
  def drag_shape_to_frame_edge(self,*args,**kwargs):calls.append((args,kwargs))
  def click_shape_center_fast(self,*args):pass
  def wait_action_settle(self,*args):yield None
 initial=dict(complete=True,showNum=1,maxNum=165,Price=200,HadPrice=33122,CanBuy=True,isEnough=True)
 gen=quantity.set_verified_common_shop_quantity(RecordingContext(),165,unit_price=200,label='necessary',assets=quantity.SACRED_SHOP_QUANTITY_ASSETS,initial_snapshot=initial,snapshot_reader=lambda:dict(initial,showNum=observed))
 assert next(gen) is None
 assert calls==[((634,quantity.SACRED_SHOP_QUANTITY_ASSETS.count_slider_track),dict(direction='right',start_ratio=0.0))]
 if observed==165:
  with pytest.raises(StopIteration) as end:
   while True:next(gen)
  assert end.value.value['quantity']==165 and end.value.value['expected_total']==33000
 else:
  with pytest.raises(RuntimeError):
   while True:next(gen)

def test_real_right_channel_scroll_frame_matches_only_paid_sku():
 fixture=json.loads((Path(__file__).parent/'fixtures/fanxiu_sacred_shop_scroll_1345.json').read_text(encoding='utf-8'))
 assert sacred_shop_product_point(fixture['tokens'],window=fixture['window'],name='弈技·仙弈盒',unit_price=200)==(169.2,701.75)
 assert sacred_shop_product_point(fixture['tokens'],window=fixture['window'],name='弈技·仙弈盒',unit_price=1300) is None
