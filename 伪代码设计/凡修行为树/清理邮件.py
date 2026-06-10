from basic import Shape, View, Runtime


class View邮件:
    def __init__(self, 标题, 时间, 状态):
        self.标题 = 标题
        # 没有识别到[时间]属性的不构建有效的'邮件'元素
        self.时间 = 时间
        # [状态]可能识别到文本，但可能有稍许偏差，需要近似匹配到'锁定'、'已阅'，如果识别不到文本也是正常的，就标记'无'即可，不能硬匹配
        self.状态 = 状态

        # 其他匹配到的shape具体位置等也要存储起来，方便后续操作
        self.标题shape
        self.状态shape


class Db邮件:
    @classmethod
    def get_邮件(cls, **筛选条件):
        """
        :return: list[Db邮件]
        """
        return 匹配的邮件清单

    def is_可领(self):
        """
        一、初规则：抓包服务存储新邮件时，填写"状态"的规则优先级
        1. 附件有物品，名称包含'法则'，"锁定"
        2. 附件有物品，名称包含'潜修心得'，"可领"
        3. 附件带保护资源，"留存"
        4. 剩下，"可领"

        保护资源指这些名称的物品：
        炼丹：炼丹灵草匣、神品灵草匣、炼丹灵草宝匣
        淬体：淬体精魄
        灵兽：珍品饲灵丸
        洗灵：洗灵奇石
        仙花：瑶池玉莲、造化青莲

        二、准规则：但是这个状态用户是可以在前端重新调整的
        所以要读取实际数据库中存储的状态为准

        三、终规则：最终领取操作是在游戏中的邮件对象为准
        （1）能按"标题/时间"匹配到邮件
            匹配到的一个或多个邮件，都是"可领"的时候，结论"可领"
            匹配到的有任意一个"留存/锁定"，则"跳过"
        （2）能按"标题"匹配到邮件
            这个标题对应的所有邮件，初规则都是"可领"，则"可领"
                这个计数实现上不能每次暴力计算，会太慢
                应该有个中介表格，存储了每个邮件名下对应的物资汇总情况（封数，总累计物资情况，初规则分布，初规则状态）
                    初规则分布统计了各个状态数量，如"锁定3/留存5/可领10"（数量为0的部分可以省略显示，简洁）
                    初规则状态相当于计算了max(状态)（锁定>留存>可领），即只要有锁定，那不管有多少留存可领，都算锁定，留存同理。
                        只有没有锁定、留存的时候，这个汇总才能算是'可领'
            否则"跳过"
        （3）匹配不到邮件
            则"跳过"
        """


def 领取邮件(runtime, 邮件: View邮件):
    """
    :param 邮件: 要领取的邮件
    """
    邮件.标题shape.click(runtime)
    yield from runtime.wait_view(122, 123)
    View(122).get_shape("领取").click(runtime)
    # 不用区分#123['删除']，这两个按钮位置一样的，操作都一样
    # 领取资源类的，会有一个领取的弹窗过度页，不用管，等待回到121就行
    yield from runtime.wait_view(121)


def 普通作业_清理邮件(runtime: Runtime):
    # 1 到达121
    runtime.goto_view(34)
    if View(68).get_shape('邮件').is_match(runtime):
        View(68).get_shape('邮件').click(runtime)
    else:
        View(34).get_shape('打开下方菜单').click(runtime)
        View(35).get_shape('邮件').wait_click(runtime)
    yield from runtime.wait_view(121)

    # 2 邮件处理
    view = View(121)
    while True:
        # 对ocr出来的文本行分组，每组的模板参考 view.get_shape('邮件模板')，其标注了子shape，刚好对应每个邮件有3个属性：[标题]、[时间]，[状态]
        邮件list: list[View邮件] = OCR识别该区域并尝试整理出结构化数据(view.get_shape("邮件清单2"))

        for 邮件 in 邮件list:
            # 锁定的不能动，已阅的后续可以一键删除不用理
            if 邮件.状态 in ("已阅", "锁定"):
                continue

            db邮件list = Db邮件.get_邮件(标题=邮件.标题, 时间=邮件.时间)
            if len(db邮件list) == 0:
                db邮件list = Db邮件.get_邮件(标题=邮件.标题)

            if len(db邮件list) == 0:
                continue  # 没有参照系，不能处理
            else:
                if not all(邮件.is_可领() for 邮件 in db邮件list):
                    continue  # 只要有一个被判定为保护，安全起见就不能领
                else:
                    yield from 领取邮件(runtime, 邮件)
                    if len(db邮件list) == 1:
                        # 原本可能就是已删，此时重复标记也没影响。这里重点是让数据库邮件状态尽量跟实际状态对齐。
                        db邮件list[0].更新状态("已删")
                    break  # 要结束此轮循环，重新ocr
        else:
            # 进入这里，表示确认该屏已经没有待处理的邮件了，需要继续加载滚动窗口内容
            yield from view.get_shape("邮件清单2").load(runtime)
            if not runtime.attrs["load_new"]:
                break  # 没有新的加载结果，已经加载完了

    # 3 清理已阅数据
    View(121).get_shape('一键删除').click(runtime)
    