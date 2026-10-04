import pytest
from backend.core.fanxiu.runtime_gui import common_shop_quantity as module
ns=vars(module)

def snapshot(quantity,**changes):
 return dict(complete=True,showNum=quantity,maxNum=165,Price=200,HadPrice=33122,CanBuy=True,isEnough=True)|changes

def execute(initial,observations):
 class Recorder:
  def __init__(self):self.drags=[];self.buttons=[]
  def drag_shape_to_frame_edge(self,*args,**kwargs):self.drags.append((args,kwargs))
  def click_shape_center_fast(self,*args):self.buttons.append(args)
  def wait_action_settle(self,*args):yield None
 ctx=Recorder(); iterator=iter(observations)
 gen=ns['set_verified_common_shop_quantity'](ctx,165,unit_price=200,label='proof',assets=module.SACRED_SHOP_QUANTITY_ASSETS,initial_snapshot=snapshot(initial),snapshot_reader=lambda:next(iterator))
 return ctx,gen

def finish(gen):
 while True:
  try:next(gen)
  except StopIteration as end:return end.value

@pytest.mark.parametrize('initial,drags,observations',[(1,1,[snapshot(91),snapshot(165)]),(91,0,[snapshot(165)]),(165,0,[snapshot(165)])])
def test_observed_maximum_residual_uses_exact_bounded_actions(initial,drags,observations):
 ctx,gen=execute(initial,observations);proof=finish(gen)
 assert len(ctx.drags)==drags
 assert len(ctx.buttons)==(0 if initial==165 else 8)
 assert all(button==(634,'+10') for button in ctx.buttons)
 assert proof['quantity']==165 and proof['expected_total']==33000

@pytest.mark.parametrize('after',[snapshot(1),snapshot(91,maxNum=166),snapshot(91,Price=201),snapshot(91,HadPrice=33000)])
def test_unbounded_or_changed_runtime_refuses_residual_actions(after):
 ctx,gen=execute(1,[after])
 with pytest.raises(RuntimeError):finish(gen)
 assert ctx.buttons==[]

def test_nonconverged_final_quantity_is_rejected():
 ctx,gen=execute(91,[snapshot(164)])
 with pytest.raises(RuntimeError):finish(gen)
 assert len(ctx.buttons)==8
