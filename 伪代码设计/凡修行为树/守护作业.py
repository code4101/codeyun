
from basic import Shape, View, Runtime

"""
行为树有如下按优先级排序的三类作业
1. 守护作业
2. 手动作业（一般是调试、开发用的临时任务）
3. 普通作业（稳定下来的业务功能代码）

每个作业，都可以理解成是一个函数，并且通过yield机制返回（return是特殊的yield）
yield每次返回一个整数，标识消耗的行动力

行为树每次行动，现在可以默认理解为总行动力=1，总行动力消耗完，就要进入下一轮tick
但因为每个作业是yield函数，所以是有状态可恢复的

每次tick，高优先级分组可以抢占，比如虽然有"普通作业"正在运行，但"守护作业"是每次tick都会提前遍历执行的
（由于现有行动力的设计机制，只有在守护阶段没有消耗行动力，才能继续正常执行手动作业、普通作业）
但是每一组里，如果已经有一个作业在运行，必须先完成这个作业，才能继续执行其他作业
"""

def 守护作业_关闭弹窗(runtime: Runtime):
    view = runtime.find_view('弹窗')

    if not view:
        return 0

    # curtime即匹配47，也匹配84，这是不矛盾的。而且这里84本来就是47的子节点，是更精细的某种指定场景的识别。
    if view.id == 47 and View(84).is_match(runtime):
        shape = View(84).get_shape('不再提示')
        if not shape.is_match(runtime):
            shape.click(runtime)
        View(84).get_shape('确认').click(runtime)
        return 1
    else:
        view.close(runtime)
        return 1
