export type DailyTaskMatchMode = 'contains' | 'exact' | 'wildcard' | 'regex';
export type DailyTaskScanPlan = 'normal' | 'candidate_rows' | 'bidirectional';

export type DailyTaskPreset = {
  id: string;
  label: string;
  query: string;
  matchMode: DailyTaskMatchMode;
  scanPlan: DailyTaskScanPlan;
  maxPages: number;
  reversePages: number;
  completedFallbackPattern: string;
  completedFallbackExcludePattern: string;
  completedFallbackMinTotal: number;
  notFoundStatus: number;
  timeoutSeconds: number;
  dragCount: number;
  requireProgress: boolean;
  legacySource: string;
  note: string;
};

const createDailyTaskPreset = (
  id: string,
  label: string,
  query: string,
  options: Partial<Omit<DailyTaskPreset, 'id' | 'label' | 'query'>> = {},
): DailyTaskPreset => ({
  id,
  label,
  query,
  matchMode: options.matchMode ?? 'contains',
  scanPlan: options.scanPlan ?? 'normal',
  maxPages: options.maxPages ?? options.dragCount ?? 20,
  reversePages: options.reversePages ?? 0,
  completedFallbackPattern: options.completedFallbackPattern ?? '',
  completedFallbackExcludePattern: options.completedFallbackExcludePattern ?? '',
  completedFallbackMinTotal: options.completedFallbackMinTotal ?? 0,
  notFoundStatus: options.notFoundStatus ?? -1,
  timeoutSeconds: options.timeoutSeconds ?? 90,
  dragCount: options.dragCount ?? 20,
  requireProgress: options.requireProgress ?? true,
  legacySource: options.legacySource ?? '',
  note: options.note ?? '',
});

export const defaultDailyTaskPresets = (): DailyTaskPreset[] => [
  createDailyTaskPreset('daily-find-yaozu', '妖族', '妖族', {
    requireProgress: false,
    legacySource: '妖族袭城.py: 日常_妖族袭城 -> 日常_查找("妖族", not_found_status=-1)',
    note: '旧版日常_妖族袭城用 require_progress=False，后续由妖族状态类读取购买次数、挑战次数。',
  }),
  createDailyTaskPreset('daily-find-daily-dungeon', '每日副本', '悟道|试炼周本|每日副本', {
    matchMode: 'regex',
    requireProgress: false,
    legacySource: '每日副本.py: 每日副本_日常入口匹配式',
    note: '入口文本可能识别成悟道、试炼周本或每日副本，后续由副本状态类处理购买和挑战次数。',
  }),
  createDailyTaskPreset('daily-find-dongtian', '洞天福地', '收取两万九|九曜玄墨', {
    matchMode: 'regex',
    legacySource: '日常功能.py: 洞天福地_进入主页 -> 日常_查找(洞天福地_日常入口匹配式)',
    note: '旧版用“收取两万九”规避完整标题 OCR 漏字；新版兼容九曜玄墨片段，命中后仍需相对读取进度。',
  }),
  createDailyTaskPreset('daily-find-lingmai', '灵脉', '参与灵脉|灵脉争夺|灵脉', {
    matchMode: 'regex',
    legacySource: '日常功能.py: 灵脉_进入主页 -> 日常_查找(灵脉_日常入口匹配式)',
    note: '旧版完整入口是参与灵脉争夺1小时，短匹配规避 OCR 漏字；入口进入后还会读取体力。',
  }),
  createDailyTaskPreset('daily-find-youli', '游历', '游历|修仙.?传|修仙.*历|传.?游', {
    matchMode: 'regex',
    scanPlan: 'candidate_rows',
    notFoundStatus: 2,
    legacySource: '日常功能.py: 日常_查找游历任务',
    note: '游历标题 OCR 不稳定，旧版使用逐屏候选扫描；找不到保持默认完成态。',
  }),
  createDailyTaskPreset('daily-find-baiye', '拜谒', '一次拜|拜谒', {
    matchMode: 'regex',
    scanPlan: 'bidirectional',
    maxPages: 8,
    reversePages: 6,
    dragCount: 14,
    notFoundStatus: 2,
    requireProgress: false,
    legacySource: '日常功能.py: 日常_拜谒 -> 点击日常拜谒任务',
    note: '拜谒完成后可能不再出现在活跃任务列表，找不到按完成态处理。',
  }),
  createDailyTaskPreset('daily-find-yaowang', '妖王', '妖王来袭', {
    legacySource: '日常功能.py: 日常_妖王来袭 -> 日常_查找("妖王来袭", not_found_status=-1)',
    note: '常规日常入口，命中后按进度/状态决定是否进入。',
  }),
  createDailyTaskPreset('daily-find-shuangxiu', '双修', '双人', {
    legacySource: '日常功能.py: 日常_双修 -> 日常_查找("双人", not_found_status=-1)',
    note: '旧版用双人稳定片段，而不是依赖完整标题。',
  }),
  createDailyTaskPreset('daily-find-lingta', '灵塔', '混沌灵塔|灵塔', {
    matchMode: 'regex',
    timeoutSeconds: 180,
    legacySource: '日常功能.py: 日常_灵塔 -> 日常_查找("混沌灵塔|灵塔", not_found_status=-1, timeout_seconds=180)',
    note: '旧版给较长超时，适合列表靠后或 OCR 波动时继续滚动查找。',
  }),
  createDailyTaskPreset('daily-find-lingzu', '灵祖', '灵祖', {
    legacySource: '日常功能.py: 日常_灵祖 -> 日常_查找("灵祖", not_found_status=-1)',
    note: '常规日常入口，进入后走灵祖挑战链路。',
  }),
  createDailyTaskPreset('daily-find-jianling', '剑灵', '挑战.*剑试', {
    matchMode: 'regex',
    requireProgress: false,
    legacySource: '日常功能.py: 日常_剑灵 -> 日常_查找("挑战.+剑试", not_found_status=-1)',
    note: '旧版注释说淬剑试炼中间字容易 OCR 错，使用稳定两端匹配。',
  }),
  createDailyTaskPreset('daily-find-xundao-lilian', '寻道历练', '寻道历练|仙侣历练|历练1次', {
    matchMode: 'regex',
    legacySource: '日常功能.py: 日常_仙侣历练；当前日常页 OCR 可见“寻道历练1次”',
    note: '旧版仙侣历练主要从大地图历练助手进入；新版先补日常页入口定位，后续再接历练助手状态机。',
  }),
  createDailyTaskPreset('daily-find-xianyuan', '挑战仙缘', '挑战\\s*仙缘|仙缘人物', {
    matchMode: 'regex',
    scanPlan: 'bidirectional',
    maxPages: 14,
    reversePages: 18,
    dragCount: 32,
    notFoundStatus: 2,
    timeoutSeconds: 180,
    completedFallbackPattern: '挑战.*仙缘|仙缘.*人物',
    completedFallbackExcludePattern: '仙缘斗法|斗法',
    completedFallbackMinTotal: 3,
    legacySource: '日常功能.py: 日常_挑战仙缘 -> 挑战仙缘_日常入口匹配式',
    note: '不要用裸仙缘兜底，避免误命中仙缘斗法；旧版还有完成行兜底扫描。',
  }),
  createDailyTaskPreset('daily-find-shouling', '首领', '击?败首领?', {
    matchMode: 'regex',
    timeoutSeconds: 180,
    legacySource: '日常功能.py: 日常_首领/首领_复核日常进度 -> 日常_查找(r"击?败首领?")',
    note: '状态和进度冲突时以进度未满优先，后续还要结合首领奖励次数和刷新 CD。',
  }),
  createDailyTaskPreset('daily-find-qixi', '奇袭', '奇袭', {
    notFoundStatus: 2,
    requireProgress: false,
    legacySource: '日常功能.py: 日常_奇袭魔界 -> 日常_查找("奇袭")',
    note: '奇袭已完成也可能继续报名次日，不能简单等同普通完成任务。',
  }),
  createDailyTaskPreset('daily-find-lundao', '论道', '论道', {
    notFoundStatus: 2,
    requireProgress: false,
    legacySource: '日常功能.py: 日常_论道 -> 日常_查找("论道")',
    note: '论道存在闻道中特殊状态，旧版 status=1 时不继续普通进入。',
  }),
  createDailyTaskPreset('daily-find-wanling', '万灵切磋', '万灵切磋', {
    notFoundStatus: 2,
    requireProgress: false,
    legacySource: '凡修助手.py: 开发中功能.日常_万灵切磋 -> 日常_查找("万灵切磋")',
    note: '旧版开发中功能，也走同一套日常入口查找模型。',
  }),
];
