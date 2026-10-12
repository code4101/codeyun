// 两层办公套房（单入户门、全室内）9 路海康监控方案的静态数据。价格为 2025–2026 年网页报价区间，仅供预算参考。

export interface CameraModel {
  id: string
  name: string
  model: string
  traits: string
}

export interface CameraPoint {
  floor: '一楼' | '二楼'
  place: string
  camera: CameraModel['id']
  reason: string
}

export interface BomItem {
  item: string
  spec: string
  qty: number
  unit: [number, number]
  note?: string
}

export const CAMERAS: CameraModel[] = [
  { id: 'door', name: '入户门警戒半球', model: 'DS-2CD3346FWDA3-IS', traits: '400 万 · 内置喇叭和麦克风 · 报警输入可接门磁 · 下班后有人进门可喊话' },
  { id: 'dome', name: '室内星光半球', model: 'DS-2CD3346WDV3-I', traits: '400 万 · 星光红外 · 宽动态 · 可吸顶也可壁装' },
]

export const POINTS: CameraPoint[] = [
  { floor: '一楼', place: '入户门内侧', camera: 'door', reason: '套房只有一个出入口，这是最关键的点位。装在门的斜对面，迎面拍清进门人员的正脸，下班后布防报警' },
  { floor: '一楼', place: '前台 / 接待区', camera: 'dome', reason: '覆盖访客等候区和前台，和入户门点位互相补充角度' },
  { floor: '一楼', place: '一楼办公区', camera: 'dome', reason: '装在角落俯拍全景，覆盖通道，不要正对工位屏幕' },
  { floor: '一楼', place: '仓库 / 样品间', camera: 'dome', reason: '覆盖货架和门口，物品进出可以追溯' },
  { floor: '一楼', place: '楼梯底部', camera: 'dome', reason: '内部楼梯是两层之间唯一的通道，从下往上拍可以串起人员动线' },
  { floor: '二楼', place: '楼梯顶部 / 二楼走廊', camera: 'dome', reason: '覆盖上楼后的走廊和各个房间的门口' },
  { floor: '二楼', place: '二楼办公区', camera: 'dome', reason: '和一楼一样装在角落俯拍全景，不对准屏幕' },
  { floor: '二楼', place: '财务室 / 档案室', camera: 'dome', reason: '对准门口和保险柜，不对准电脑屏幕' },
  { floor: '二楼', place: '弱电间 / 储物间', camera: 'dome', reason: 'NVR 和网络设备放在这里，同时看住设备，防止有人连录像机一起拿走' },
]

export const BOM: BomItem[] = [
  { item: '入户门警戒半球', spec: 'DS-2CD3346FWDA3-IS', qty: 1, unit: [450, 550], note: '没查到公开报价，这是估算' },
  { item: '室内星光半球', spec: 'DS-2CD3346WDV3-I 2.8mm', qty: 8, unit: [330, 460] },
  { item: '16 路 PoE 录像机', spec: 'DS-7616N-E2/16P-V3（16 路 · 2 盘位 · 16 口 PoE）', qty: 1, unit: [1500, 2200], note: '没查到 PoE 版的公开报价，这是估算（无 PoE 版约 ¥1,250）' },
  { item: '监控硬盘', spec: '西数紫盘 8TB（WD85PURZ）或希捷酷鹰 8TB', qty: 2, unit: [1100, 2700], note: '行情波动很大，下单前询价；预算紧张可以先装 1 块，开启 H.265+' },
  { item: '网线', spec: '纯铜六类网线 305 米/箱', qty: 1, unit: [400, 700] },
  { item: '辅材', spec: '水晶头、线槽/PVC 管、吸顶支架、监控提示牌、带锁的设备柜', qty: 1, unit: [300, 700] },
  { item: 'UPS', spec: '1000VA，供 NVR、交换机和光猫使用', qty: 1, unit: [500, 900] },
  { item: '安装施工', spec: '外包按点位计，约 ¥100–200/点；自己安装可省', qty: 9, unit: [100, 200] },
]

export const CHECKLISTS: { title: string; items: string[] }[] = [
  {
    title: '物业与公共区域',
    items: [
      '楼道、电梯厅、大堂属于公共区域，由物业负责监控；私自在门外安装摄像头容易引起纠纷，一般需要物业同意',
      '物业的录像通常不能直接拷贝。出了事先报警，由警方向物业调取；所以门内的点位要自己装好',
      '想看门外情况，可以选带猫眼摄像头的智能门锁，或者装电子猫眼；但不要长时间对着对门录像，这类隐私纠纷不少',
      '钻吊顶、走线之前先向物业报备装修施工，不要打穿公共墙体或楼板',
    ],
  },
  {
    title: '合规（公司场景）',
    items: [
      '在入户门和监控区域显著位置张贴“视频监控区域”提示标识（《个人信息保护法》第 26 条）',
      '卫生间、更衣室、休息室、哺乳室等私密区域禁止安装',
      '不要对准员工的电脑屏幕和键盘',
      '拾音比录像更敏感：办公区默认关闭拾音，确实需要时要在规章中明确告知',
      '把监控用途、范围、保存期限和调阅流程写进公司规章制度，并告知员工',
      '录像只允许指定人员调阅并留下记录；对外提供（例如给警方）要走书面流程',
      '录像通常保存 30 天以上；所在行业或地方有更高要求的，按其规定执行',
    ],
  },
  {
    title: '安装施工',
    items: [
      '网线走吊顶或线槽；装修已经完成的，用明装线槽贴着墙角走，比较整洁',
      '两层之间的网线沿内部楼梯或预留的管道走，统一拉到放 NVR 的弱电间',
      '入户门那一路安装高度在 2.2–2.5 米，在门的斜对面，不要太俯视，否则只能拍到头顶',
      '靠窗的点位背对窗户，避免逆光看不清人脸',
      'NVR 放在带锁的柜子里或者藏起来，防止有人连录像一起拿走；下班布防后报警推送到手机',
      'NVR、交换机和光猫都接 UPS，停电时也能继续录像',
      '入户门那一路开启人形侦测，只在下班时段布防，接上门磁后效果更好',
    ],
  },
  {
    title: '网络与采购',
    items: [
      '摄像头接在 NVR 自带的 PoE 口上，PoE 口是独立网段，摄像头不会直接暴露在办公网',
      '激活设备时设置强密码；关闭 UPnP，不做端口映射；远程查看用官方的“海康互联”App',
      'NVR 管理员账号只给负责人，其他人分配只能预览/回放的子账号',
      '在海康官方旗舰店或授权经销商购买，注意区分国内版和海外版固件',
      '硬盘要买监控盘（紫盘或酷鹰），小心 OEM 盘冒充零售盘（没有官方保修）',
      '只装 8 路的话，可以换成 8 路 PoE NVR，省几百元，但以后就没有扩展余量了',
    ],
  },
]

export const SOURCES: { title: string; url: string }[] = [
  { title: '中关村在线 · DS-2CD3346WDV3-I 报价', url: 'https://detail.zol.com.cn/camera_equipment/index1440959.shtml' },
  { title: '苏宁 · DS-7616N-E2-V3 参数', url: 'https://www.suning.com/itemcanshu/0000000000/12301753064.html' },
  { title: '京东 · 16 路 NVR 新款汇总', url: 'https://www.jd.com/xinkuan/670e220637f7ba07eae.html' },
  { title: '道通存储 · 2026 年 4 月西数监控盘行情', url: 'https://www.dtstor.com/05_4968.html' },
  { title: '什么值得买 · 西数监控硬盘', url: 'https://www.smzdm.com/ju/sp8m109/' },
]
