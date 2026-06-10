"""
凡修脚本，凡修行为树，通用基础底座构建
"""
import time

class MatchRole:
    off = 0  # 不参与
    required = 1  # 必须匹配
    decisive = 2  # 任一命中即可确认


class Shape:
    # 一个标注数据框
    def __init__(self):
        self.parent_view: View  # 标记所属的view
        self.parent_shape = None  # shape标注也是支持嵌套数结构的，所以可能有父节点
        self.场景标识: MatchRole
        self.图像匹配: MatchRole
        self.OCR匹配: MatchRole

    def is_match(self, runtime):
        """
        判断当前shape是否匹配当前帧画面
        :return: 是否匹配

        如果有'抠图'配置，图像、ocr都要在抠图基础上，去掉背景干扰进行处理
        还有抖动、浮动、容差、区分等等因素，要综合考虑，这个本身也是一个大功能函数
        """
        cur_view = runtime.get_cur_view()

    def click(self, runtime):
        """点击当前shape中心坐标，默认可以有半斤为3的圆形区域随机扰动"""

    def wait_click(self, runtime):
        """等待目标出现后点击"""

    def load(self, runtime, ratio=0.5, duration=1.5):
        """仅针对有'内容方向'的shape的滚动窗口加载功能

        用runtime.attrs['load_new']来标记是否加载了新内容

        先获取x的'内容方向'属性，'无'就是无法拖拽的，标记false
        否则比如是'下'，表示了内容扩展方向。表示往上拖拽，下方还有内容，标记true。
        以shape顶部截取一个10%左右面积的区域图像（函数运行前就获取了），拖拽后，再获取，如果两次图片的相似度90%，则认为内容已经加载完了，标记false
        拖拽默认50%比例（比如'下'的情况，可以垂直居中位置，从75%的坐标位置移动到25%），动作时间1.5秒

        拖拽后要固定等2秒，这样再取图像哈希等操作才稳定
        """
        runtime.attrs['load_new'] = False

        if self.attrs["内容方向"] == "无":
            return 0
        ...

        # 拖拽操作要触发一次消耗行动力标记
        yield 1

        # 根据规则标记false或true
        runtime.attrs['load_new'] = True

class View:
    # 数据库中存储的一帧画面
    def __init__(self, id: int = None):
        self.id = id  # 每帧都有唯一标识id

    def get_shapes(self, **其他筛选条件):
        """
        :param 筛选条件:
            场景标识： MatchRole枚举值。所以支持"场景标识>0"等这样的筛选逻辑
        :return: list[shape]，在当前view中，所有已标注的shapes数据
        """

    def get_shape(self, title=None, **其他筛选条件):
        """
        :param title: 标题匹配（不是包含）
        :return: 只返回第1个匹配的shape
        """

    def is_match(self, runtime):
        """
        借助"场景标识"来判断画面帧是否匹配

        :param View|int view: 可以是View对象，也可以是对应的id（此时要升级为view）
        :return: 是否和view中的指定帧id匹配
        """
        shapes = self.get_shapes(场景标识不小于=1)
        if len(shapes) == 0:  # 没有场景标识的不能作为锚点view
            return False

        shapes1: list[Shape] = 筛选出场景标识为1的(shapes)
        shapes2: list[Shape] = 筛选出场景标识为2的(shapes)

        for shape in shapes2:  # 任何一个能匹配都通过
            if shape.is_match(runtime):
                return True

        # 否则剩下的shapes1必须全部都匹配才能通过
        if not shapes1:
            return False

        for shape in shapes1:
            if not shape.is_match(runtime):
                return False
        return True

    def close(self, runtime):
        """尝试关闭当前帧

        按'关闭/退出/空白/确认'的优先级顺序，依次找对应标题的shape
            '空白'一般是一些场景view的背景区域，点击背景区域也可以关闭弹窗等元素
                会比直接点元素太小的'关闭'更安全能有效触发
                也比点'确认'更安全能避免进入场景跳转移动
        找到第1个匹配的shape后，点击它中心点（可以带半斤3像素的随机扰动）
        """


class Runtime:
    def get_cur_view(self, update=False):
        """
        :param update:
            默认行为树每次tick会更新一次cur_view，只取缓存
            update=True可以强制当前刷新一下
        :return: 当前帧画面
        """

    def get_views(self, group="", 递归查找=False):
        """
        :param group: 只获取指定组的views
            参考：http://localhost:5173/fanxiu/data-annotation?window=mumu&entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2
            这里有个帧树结构，有按'目录'名的分组整理结构。
            可以输入'日常/报名'，也可以输入'报名'等，都会按'相对目录'的逻辑找到目标分组。
            仅输入'报名'可以，也可以'日常/报名'这样更精确的定位

            空字符串''的时候，标识返回所有帧
        :param 递归查找: 是否递归查找子分组，默认只找直接子节点的view，不查找嵌套情况
        :return: list[view]
        """

    def find_view(self, group=""):
        """
        :param group: 只在限定组内检索
            空字符串''的时候，只对第一层shape尝试匹配，带嵌套情况，父节点是shape的shape跳过
        :return: 在view中匹配的场景view
        """
        views: list[View] = self.get_views(group)
        # 这里根据views数量开对等数量的多线程，并行执行，但view是有顺序关系的，只能返回第1个匹配的view
        for view in views:
            if view.is_match(self):
                return view

    def wait_view(self, *view_ids, timeout=None):
        """
        等待指定view出现(可以是一组，只要有任意一个匹配出现就终止)
        :timeout: 可以设置等待时间上限
        """
        while True:
            for view_id in view_ids:
                if view_id.is_match(self):
                    return 0
            else:
                time.sleep(1)  # 这里注意对timeout的判定相关功能实现，伪代码省略体现
                yield 1

    def goto_view(self, view_id):
        """
        场景移动，从cur_view移动到目标view位置

        注意移动的路径，频数，需要更新到'场景跳转'属性中
        为了稳定性，这个功能的实现过程中，应该也是要用到wait_view增强鲁棒性的，进行合理的等待
        """
