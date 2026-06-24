from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import UnityPy

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.mystia.catalog import CATALOG_VERSION, DEFAULT_ANALYSIS_ROOT

DEFAULT_GAME_ROOT = Path(r"D:\SteamLibrary\steamapps\common\Touhou Mystia Izakaya")
AA_DIR = Path(r"Touhou Mystia Izakaya_Data\StreamingAssets\aa\StandaloneWindows64")
CORE_PROFILE_BUNDLE = "core_07e01badce0c3466a71d003dd46efa15.bundle"
SIMPLIFIED_LANG_BUNDLE = "core_d588e1cad1b8b9b47f46af2be495e6c3.bundle"
XOR_KEY = 0x53
ASSET_IMAGE_DIR = "assets/images"
ASSET_AUDIO_DIR = "assets/audio"


def _safe_asset_stem(value: str) -> str:
    sanitized = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value, flags=re.UNICODE).strip("._")
    return sanitized or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _decode_bundle_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw.startswith(b"UnityFS"):
        return raw
    decoded = bytes(byte ^ XOR_KEY for byte in raw)
    if not decoded.startswith(b"UnityFS"):
        raise RuntimeError(f"bundle 解混淆后不是 UnityFS：{path}")
    return decoded


def _load_bundle(path: Path):
    return UnityPy.load(io.BytesIO(_decode_bundle_bytes(path)))


def _load_bundles(paths: list[Path]):
    streams = [io.BytesIO(_decode_bundle_bytes(path)) for path in paths]
    return UnityPy.load(*streams)


def _read_named_text_assets(bundle_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for obj in _load_bundle(bundle_path).objects:
        if obj.type.name != "TextAsset":
            continue
        data = obj.read()
        result[str(data.m_Name)] = str(data.m_Script)
    return result


def _parse_tsv(text: str) -> list[dict[str, str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    rows = list(csv.DictReader(io.StringIO(normalized), delimiter="\t"))
    return [
        {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
        for row in rows
    ]


def _parse_spell_rows(text: str) -> dict[int, list[dict[str, Any]]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    result: dict[int, list[dict[str, Any]]] = {}
    reader = csv.reader(io.StringIO(normalized), delimiter="\t")
    header = next(reader, [])
    if not header:
        return result
    for raw_row in reader:
        if not raw_row or not str(raw_row[0]).strip():
            continue
        try:
            guest_id = int(str(raw_row[0]).strip())
        except ValueError:
            continue
        spells: list[dict[str, Any]] = []
        for index, cell in enumerate(raw_row[1:], start=1):
            value = str(cell or "").strip()
            if not value:
                continue
            name, _, description = value.partition("|")
            spell_type = "reward" if index == 1 else "punishment" if index == 2 else "extra"
            spells.append({
                "slot": index,
                "type": spell_type,
                "type_label": "奖励符卡" if spell_type == "reward" else "惩罚符卡" if spell_type == "punishment" else "额外符卡",
                "name": name.strip(),
                "description": description.strip(),
            })
        result[guest_id] = spells
    return result


def _parse_special_conversations(text: str) -> dict[int, list[str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    result: dict[int, list[str]] = {}
    reader = csv.reader(io.StringIO(normalized), delimiter="\t")
    next(reader, None)
    for row in reader:
        if len(row) < 3:
            continue
        try:
            guest_id = int(str(row[0]).strip())
        except ValueError:
            continue
        result[guest_id] = [
            value.strip()
            for value in row[2:]
            if value and value.strip()
        ]
    return result


def _parse_special_evaluations(text: str) -> dict[int, dict[str, str]]:
    rows = _parse_tsv(text)
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        try:
            guest_id = int(row.get("id", ""))
        except ValueError:
            continue
        result[guest_id] = {
            key: value
            for key, value in row.items()
            if key != "id" and value
        }
    return result


def _parse_special_tag_lines(text: str) -> dict[int, list[str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    result: dict[int, list[str]] = {}
    reader = csv.reader(io.StringIO(normalized), delimiter="\t")
    next(reader, None)
    for row in reader:
        if not row:
            continue
        try:
            guest_id = int(str(row[0]).strip())
        except ValueError:
            continue
        result[guest_id] = [
            value.strip()
            for value in row[1:]
            if value and value.strip()
        ]
    return result


def _parse_request_lines(text: str) -> list[str]:
    rows = _parse_tsv(text)
    result: list[str] = []
    for row in rows:
        content = row.get("Content") or row.get("content") or row.get("Text") or row.get("text") or ""
        if content:
            result.append(content)
    return result


def _zh_text_score(lines: list[str]) -> int:
    text = "\n".join(lines)
    han = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    kana = sum(1 for char in text if "\u3040" <= char <= "\u30ff")
    hangul = sum(1 for char in text if "\uac00" <= char <= "\ud7af")
    return han - kana * 3 - hangul * 3


def _simplified_zh_score(lines: list[str]) -> int:
    text = "\n".join(lines)
    simplified_chars = set("与你丑专东丝丢两严丧个临为丽举么义乌乐乔习乡书买乱争于亏云亚产亩亲亵亿仅从仑仓仪们价众优伙会伞伟传伤伦伪体余佣侠侣侥侦侧侨侩侪侬俣俦俨俩俪俭债倾偬偻偾偿傥傧储傩儿兑兖兰关兴养兽冁内冈册写军农冯冲决况冻净凄准凉减凑凛几凤凫凭凯击凿刍划刘则刚创删别刬刭刹剂剐剑剥剧劝办务动励劲劳势勋匀匦匮区医华协单卖卢卤卫却厂厅历厉压厌厍厕厢厣厦厨厩厮县参叆叇双发变叙叠叶号叹叽吁后吓吕吗吣吨听启吴呐呒呓呕呖呗员呙呛呜咏咙咛咝咤响哑哒哓哔哕哗哙哜哝哟唛唠唡唢唤啧啬啭啮啰啴啸喷喽喾嗫嗳嘘嘤嘱噜嚣团园囱围囵国图圆圣圹场坏块坚坛坝坞坟坠垄垅垆垒垦垩垫垭垱垲垴埘埙埚埯堑堕墙壮声壳壶壸处备复够头夹夺奁奂奋奖奥妆妇妈妩妪妫姗姜娄娅娆娇娈娱娲娴婳婴婵婶媪嫒嫔嫱嬷孙学孪宁宝实宠审宪宫宽宾寝对寻导寿将尔尘尝尧尴尸尽层屃屉届属屡屦屿岁岂岖岗岘岙岚岛岭岳岽岿峃峡峣峤峥峦崂崃崄崭嵘嵚嵛嵝巅巩巯币帅师帏帐帘帜带帧帮帱帻帼幂庄庆庐庑库应庙庞废庼廪开异弃张弥弦弪弯弹强归当录彦彻径徕忆忏忧忾怀态怂怃怄怅怆怜总怼怿恋恳恶恸恹恺恻恼恽悦悫悬悭悯惊惧惨惩惫惬惭惮惯愠愤愦愿慑懑懒懔戆戋戏戗战戬户扎扑扦执扩扪扫扬扰抚抛抟抠抡抢护报担拟拢拣拥拦拧拨择挂挚挛挜挝挞挟挠挡挢挣挤挥挦挽捞损捡换捣据捻掳掴掷掸掺掼揽揾揿搀搁搂搅携摄摅摆摇摈摊撄撑撵撷撸撺擞攒敌敛数斋斓斗斩断无旧时旷旸昙昼昽显晋晒晓晔晕晖暂暧术机杀杂权杆条来杨杩杰极构枞枢枣枥枧枨枪枫枭柜柠查柽栀栅标栈栉栊栋栌栎栏树栖样栾桠桡桢档桤桥桦桧桨桩梦梼梾检棂椁椟椠椤椭楼榄榅榇榈榉槚槛槟槠横樯樱橥橱橹橼檩欢欤欧歼殁殇残殒殓殚殡殴毁毂毕毙毡毵氇气氢氩氲汇汉污汤汹沟没沣沤沥沦沧沪泞泪泶泷泸泺泻泼泽泾洁洒洼浃浅浆浇浈浊测济浏浐浑浒浓浔涛涝涞涟涠涡涢涣涤润涧涨涩淀渊渌渍渎渐渑渔渖渗温游湾湿溃溅溆滗滚滞滟滠满滢滤滥滦滨滩滪漤潆潇潋潍潜潴澜濑濒灏灭灯灵灾灿炀炉炖炜炝点炼炽烁烂烃烛烟烦烧烨烩烫烬热焕焖焘煴爱爷牍牦牵牺犊状犷犸犹狈狝狞独狭狮狯狰狱狲猃猎猕猡猪猫猬献獭玑玛玮环现玱玺珐珑珰珲琎琏琐琼瑶瑷璎瓒瓮电画畅畴疖疗疟疠疡疬疮疯疱疴痈痉痒痨痪痫瘅瘆瘗瘘瘪瘫瘾瘿癞癣癫皑皱皲盏盐监盖盗盘眍眦眬着睁睐睑瞒瞩矫矶矾矿砀码砖砗砚砜砺砻砾础硕硖硗硙确硷碍碛碜碱礼祃祎祢祯祷祸禀禄禅离秃秆种积称秽秾稆税稣稳穑穷窃窍窑窜窝窥窦竖竞笃笋笔笕笺笼笾筑筚筛筜筝筹签简箓箦箧箨箩箪箫篑篓篮篱簖籁籴类籼粜粝粤粪粮糁糇紧絷纟纠纡红纣纤纥约级纨纩纪纫纬纭纯纰纱纲纳纵纶纷纸纹纺纽纾线绀绁绂练组绅细织终绉绊绋绌绍绎经绑绒结绔绕绖绗绘给绚绛络绝绞统绠绡绢绣绥绦继绨绩绪绫续绮绯绰绱绲绳维绵绶绷绸绺绻综绽绾绿缀缁缂缃缄缅缆缇缈缉缊缋缌缍缎缏缐缑缒缓缔缕编缗缘缙缚缛缜缝缟缠缡缢缣缤缥缦缧缨缩缪缫缬缭缮缯缰缱缲缳缴罂网罗罚罢罴羁羟羡翘耢耧耸耻聂聋职聍联聪肃肠肤肮肴肾肿胀胁胆胜胧胨胪胫胶脉脍脏脐脑脓脔脚脱脶脸腊腌腘腭腻腼腽腾膑臜舆舣舰舱舻艰艳艺节芈芗芜芦苁苇苈苋苌苍苎苏苹茎茏茑茔茕茧荆荐荙荚荛荜荞荟荠荡荣荤荥荦荧荨荩荪荫荬荭荮药莅莱莲莳莴莶获莸莹莺莼萝萤营萦萧萨葱蒇蒉蒋蒌蓝蓟蓠蓣蓥蓦蔷蔹蔺蔼蕲蕴薮藓蘖虏虑虚虫虽虾虬虮虱蛊蛎蛏蛮蛰蛱蛲蛳蛴蜕蜗蝇蝈蝉蝎蝼蝾衅衔补衬衮袄袅袆袜袭袯装裆裢裣裤裥褛褴襁襕见观规觅视觇览觉觊觋觌觎觏觐觑角觞触觯讠订讣认讥讦讧讨让讪讫训议讯记讱讲讳讴讵讶讷许讹论讼讽设访诀证诂诃评诅识诈诉诊诋诌词诎诏译诒诓诔试诗诖诚诛诜话诞诟诠诡询诣诤该详诧诨诩诫诬语诮误诰诱诲诳说诵诶请诸诹诺读诼诽课诿谀谁谂调谄谅谆谈谊谋谌谍谎谏谐谑谒谓谔谕谖谗谘谙谚谛谜谝谞谟谠谡谢谣谤谥谦谧谨谩谪谫谬谭谮谯谰谱谲谳谴谵谷豮贝贞负贡财责贤败账货质贩贪贫贬购贮贯贰贱贲贳贴贵贶贷贸费贺贻贼贽贾贿赀赁赂赃资赅赆赇赈赉赊赋赌赍赎赏赐赓赔赕赖赗赘赙赚赛赜赝赞赠赡赢赣赵赶趋趱趸跃跄跞践跶跷跸跹跻踊踌踪踬踯蹑蹒蹰蹿躏躜躯车轧轨轩轪软轰轱轲轳轴轵轶轷轸轹轺轻轼载轾轿辀较辄辅辆辇辈辉辊辋辍辎辏辐辑输辔辕辖辗辘辙辚辞辟辩辫边辽达迁过迈运还这进远违连迟迩迳迹适选逊递逻遗遥邓邝邬邮邹邺邻郁郏郑郓郦郧郸酝酦酱酽酾酿释里鉴銮针钉钊钋钌钍钎钏钐钒钓钔钕钗钙钚钛钜钝钞钟钠钡钢钥钦钧钨钩钪钫钬钭钮钯钰钱钲钳钴钵钶钷钸钹钺钻钼钽钾钿铀铁铂铃铄铅铆铈铉铊铋铌铍铎铐铑铒铕铖铗铙铛铜铝铞铟铠铡铢铣铤铥铦铧铨铩铪铫铬铭铮铯铰铱铲铳铴铵银铷铸铹铺铻铼铽链铿销锁锂锃锄锅锆锇锈锉锋锌锍锎锏锐锑锒锓锔锕锖锗错锚锛锜锞锟锠锡锢锣锤锥锦锨锫锭键锯锰锱锲锵锶锷锸锹锺锻锼锾锿镀镁镂镃镄镅镇镈镉镊镋镌镍镎镏镐镑镒镓镔镖镗镘镙镚镜镝镞镟镠镡镢镣镤镥镦镧镨镩镪镫镬镭镯镰镱镲镳镴长门闩闪闫闭问闯闰闲间闵闷闸闹闺闻闼闽闾阀阁阂阃阄阅阆阈阉阊阋阌阍阎阏阐阑阒阔阕阖阗阘阙阚队阳阴阵阶际陆陇陈陉陕陧陨险随隐隶隽难雏雠雳雾霁霉霭靓静靥鞑鞒鞯韦韧韨韩韪韫韬韵页顶顷项顺须顼顾顿颀颁颂预颅领颇颈颉颊颌颍颎颏颐频颓颗题额颚颜颛颞颟颠颡颢颤颥颦风飏飐飒飓飔飕飖飘飙飚飞飨餍饣饥饧饨饩饪饫饬饭饮饯饰饱饲饳饴饵饶饷饸饹饺饻饼饽饿馀馁馂馃馄馅馆馈馊馋馍馎馏馐馑馒馓馔馕马驭驮驯驰驱驳驴驵驶驷驸驹驻驺驾驿骀骁骂骄骅骆骇骈骊验骏骐骑骒骓骖骗骘骚骛骜骝骞骟骠骡骢骣骤骥骦骧髅髋髌鬓魇魉鱼鱿鲁鲂鲅鲆鲇鲈鲋鲍鲎鲐鲑鲒鲔鲕鲚鲛鲜鲞鲟鲠鲡鲢鲣鲤鲥鲦鲧鲨鲩鲫鲭鲮鲰鲱鲲鲳鲴鲵鲶鲷鲸鲻鲼鲽鳄鳅鳆鳇鳌鳍鳎鳏鳐鳓鳔鳕鳖鳗鳘鳙鳜鳝鳞鳟鳡鳢鸟鸠鸡鸢鸣鸥鸦鸨鸩鸪鸫鸬鸭鸯鸱鸲鸳鸵鸶鸷鸸鸹鸺鸽鸾鸿鹁鹂鹃鹄鹅鹆鹇鹈鹉鹊鹋鹌鹏鹐鹑鹒鹕鹗鹘鹚鹛鹜鹞鹣鹤鹦鹧鹨鹩鹪鹫鹬鹭鹰鹱鹲鹳麦麸黄黉黡黩黪黾鼋鼍鼗鼹齐齑齿龀龃龄龅龆龇龈龉龊龌龙龚龛龟")
    traditional_chars = set("與妳醜專東絲丟兩嚴喪個臨為麗舉麼義烏樂喬習鄉書買亂爭於虧雲亞產畝親褻億僅從侖倉儀們價眾優夥會傘偉傳傷倫偽體餘傭俠侶僥偵側僑儈儕儂俁儔儼倆儷儉債傾傯僂僨償儻儐儲儺兒兌兗蘭關興養獸囅內岡冊寫軍農馮沖決況凍淨淒準涼減湊凜幾鳳鳧憑凱擊鑿芻劃劉則剛創刪別剗剄剎劑剮劍剝劇勸辦務動勵勁勞勢勳勻匭匱區醫華協單賣盧鹵衛卻廠廳歷厲壓厭厙廁廂厴廈廚廄廝縣參靉靆雙發變敘疊葉號嘆嘰籲後嚇呂嗎唚噸聽啟吳吶嘸囈嘔嚦唄員咼嗆嗚詠嚨嚀噝吒響啞噠嘵嗶噦嘩噲嚌噥喲嘜嘮啢嗩喚嘖嗇囀齧囉嘽嘯噴嘍嚳囁噯噓嚶囑嚕囂團園囪圍圇國圖圓聖壙場壞塊堅壇壩塢墳墜壟壠壚壘墾堊墊埡壋塏堖塒塤堝垵塹墮牆壯聲殼壺壼處備復夠頭夾奪奩奐奮獎奧妝婦媽嫵嫗媯姍薑婁婭嬈嬌孌娛媧嫻嫿嬰嬋嬸媼嬡嬪嬙嬤孫學孿寧寶實寵審憲宮寬賓寢對尋導壽將爾塵嘗堯尷屍盡層屭屜屆屬屢屨嶼歲豈嶇崗峴嶴嵐島嶺嶽崬巋嶨峽嶢嶠崢巒嶗崍嶮嶄嶸嶔崳嶁巔鞏巰幣帥師幃帳簾幟帶幀幫幬幘幗冪莊慶廬廡庫應廟龐廢廎廩開異棄張彌弦弳彎彈強歸當錄彥徹徑徠憶懺憂愾懷態慫憮慪悵愴憐總懟懌戀懇惡慟懨愷惻惱惲悅愨懸慳憫驚懼慘懲憊愜慚憚慣慍憤憒願懾懣懶懍戇戔戲戧戰戩戶紮撲扦執擴捫掃揚擾撫拋摶摳掄搶護報擔擬攏揀擁攔擰撥擇掛摯攣掗撾撻挾撓擋撟掙擠揮撏輓撈損撿換搗據撚擄摑擲撣摻摜攬搵撳攙擱摟攪攜攝攄擺搖擯攤攖撐攆擷擼攛擻攢敵斂數齋斕鬥斬斷無舊時曠暘曇晝曨顯晉曬曉曄暈暉暫曖術機殺雜權桿條來楊榪傑極構樅樞棗櫪梘棖槍楓梟櫃檸查檉梔柵標棧櫛櫳棟櫨櫟欄樹棲樣欒椏橈楨檔榿橋樺檜槳樁夢檮棶檢欞槨櫝槧欏橢樓欖榲櫬櫚櫸檟檻檳櫧橫檣櫻櫫櫥櫓櫞檁歡歟歐殲歿殤殘殞殮殫殯毆毀轂畢斃氈毿氌氣氫氬氳匯漢汙湯洶溝沒灃漚瀝淪滄滬濘淚澩瀧瀘濼瀉潑澤涇潔灑窪浹淺漿澆湞濁測濟瀏滻渾滸濃潯濤澇淶漣潿渦溳渙滌潤澗漲澀澱淵淥漬瀆漸澠漁瀋滲溫遊灣濕潰濺漵潷滾滯灩灄滿瀅濾濫灤濱灘澦濫瀠瀟瀲濰潛瀦瀾瀨瀕灝滅燈靈災燦煬爐燉煒熗點煉熾爍爛烴燭煙煩燒燁燴燙燼熱煥燜燾熅愛爺牘犛牽犧犢狀獷獁猶狽獮獰獨狹獅獪猙獄猻獫獵獼玀豬貓蝟獻獺璣瑪瑋環現瑲璽琺瓏璫琿璡璉瑣瓊瑤璦瓔瓚甕電畫暢疇癤療瘧癘瘍癧瘡瘋皰痾癰痙癢癆瘓癇癉瘮瘞瘺癟癱癮癭癩癬癲皚皺皸盞鹽監蓋盜盤瞘眥矓著睜睞瞼瞞矚矯磯礬礦碭碼磚硨硯碸礪礱礫礎碩硤磽磑確鹼礙磧磣鹼禮禡禕禰禎禱禍稟祿禪離禿稈種積稱穢穠穭稅穌穩穡窮竊竅窯竄窩窺竇豎競篤筍筆筧箋籠籩築篳篩簹箏籌簽簡籙簀篋籜籮簞簫簣簍籃籬籪籟糴類秈糶糲粵糞糧糝餱緊縶糾紆紅紂纖紇約級紈纊紀紉緯紜純紕紗綱納縱綸紛紙紋紡紐紓線紺紲紱練組紳細織終縐絆紼絀紹繹經綁絨結絝繞絰絎繪給絢絳絡絕絞統綆綃絹繡綏絛繼綈績緒綾續綺緋綽緔緄繩維綿綬繃綢綹綣綜綻綰綠綴緇緙緗緘緬纜緹緲緝縕繢緦綞緞緶線緱縋緩締縷編緡緣縉縛縟縝縫縞纏縭縊縑繽縹縵縲纓縮繆繅纈繚繕繒韁繾繰繯繳罌網羅罰罷羆羈羥羨翹耮耬聳恥聶聾職聹聯聰肅腸膚骯餚腎腫脹脅膽勝朧腖臚脛膠脈膾髒臍腦膿臠腳脫腡臉臘醃膕齶膩靦膃騰臏臢輿艤艦艙艫艱艷藝節羋薌蕪蘆蓯葦藶莧萇蒼苧蘇蘋莖蘢蔦塋煢繭荊薦薘莢蕘蓽蕎薈薺蕩榮葷滎犖熒蕁藎蓀蔭蕒葒葤藥蒞萊蓮蒔萵薟獲蕕瑩鶯蓴蘿螢營縈蕭薩蔥蕆蕢蔣蔞藍薊蘺蕷鎣驀薔蘞藺藹蘄蘊藪蘚櫱虜慮虛蟲雖蝦虯蟣蝨蠱蠣蟶蠻蟄蛺蟯螄蠐蛻蝸蠅蟈蟬蠍螻蠑釁銜補襯袞襖裊褘襪襲襏裝襠褳襝褲襇褸襤繈襴見觀規覓視覘覽覺覬覡覿覦覯覲覷觴觸觶訂訃認譏訐訌討讓訕訖訓議訊記訒講諱謳詎訝訥許訛論訟諷設訪訣證詁訶評詛識詐訴診詆謅詞詘詔譯詒誆誄試詩詿誠誅詵話誕詬詮詭詢詣諍該詳詫諢詡誡誣語誚誤誥誘誨誑說誦誒請諸諏諾讀諑誹課諉諛誰諗調諂諒諄談誼謀諶諜謊諫諧謔謁謂諤諭諼讒諮諳諺諦謎諞諝謨讜謖謝謠謗諡謙謐謹謾謫譾謬譚譖譙讕譜譎讞譴讖穀豶貝貞負貢財責賢敗賬貨質販貪貧貶購貯貫貳賤賁貰貼貴貺貸貿費賀貽賊贄賈賄貲賃賂贓資賅贐賕賑賚賒賦賭齎贖賞賜賡賠賧賴賵贅賻賺賽賾贗贊贈贍贏贛趙趕趨趲躉躍蹌躒踐躂蹺蹕躚躋踴躊蹤躓躑躡蹣躕躥躪躦軀車軋軌軒軑軟轟軲軻轤軸軹軼軤軫轢軺輕軾載輊轎輈較輒輔輛輦輩輝輥輞輟輜輳輻輯輸轡轅轄輾轆轍轔辭闢辯辮邊遼達遷過邁運還這進遠違連遲邇逕跡適選遜遞邏遺遙鄧鄺鄔郵鄒鄴鄰鬱郟鄭鄆酈鄖鄲醞醱醬釅釃釀釋裏鑒鑾針釘釗釙釕釷釺釧釤釩釣鍆釹釵鈣鈈鈦鉅鈍鈔鐘鈉鋇鋼鑰欽鈞鎢鉤鈧鈁鈥鈄鈕鈀鈺錢鉦鉗鈷缽鈳鉕鈽鈸鉞鑽鉬鉭鉀鈿鈾鐵鉑鈴鑠鉛鉚鈰鉉鉈鉍鈮鈹鐸銬銠鉺銪鋮鋏鐃鐺銅鋁銱銦鎧鍘銖銑鋌銩銛鏵銓鎩鉿銚鉻銘錚銫鉸銥鏟銃鐋銨銀銣鑄鐒鋪鋙錸鋱鏈鏗銷鎖鋰鋥鋤鍋鋯鋨鏽銼鋒鋅鋶鐦鐧銳銻鋃鋟鋦錒錆鍺錯錨錛錡錁錕錫錮鑼錘錐錦鍁錇錠鍵鋸錳錙鍥鏘鍶鍔鍤鍬鍾鍛鎪鍰鎄鍍鎂鏤鎡鐨鎇鎮鎛鎘鑷钂鐫鎳鎿鎦鎬鎊鎰鎵鑌鏢鏜鏝鏍鏰鏡鏑鏃鏇鏐鐔钁鐐鏷鑥鐓鑭鐠鑹鏹鐙鑊鐳鐲鐮鐿鑔鑣鑞長門閂閃閆閉問闖閏閒間閔悶閘鬧閨聞闥閩閭閥閣閡閫鬮閱閬閾閹閶鬩閿閽閻閼闡闌闃闊闋闔闐闒闕闞隊陽陰陣階際陸隴陳陘陝隉隕險隨隱隸雋難雛讎靂霧霽黴靄靚靜靨韃鞽韉韋韌韍韓韙韞韜韻頁頂頃項順須頊顧頓頎頒頌預顱領頗頸頡頰頜潁熲頦頤頻頹顆題額顎顏顓顳顢顛顙顥顫顬顰風颺颭颯颶颸颼颻飄飆飛饗饜飢餳飩餼飪飫飭飯飲餞飾飽飼飿飴餌饒餉餄餎餃餏餅餑餓餘餒餕餜餛餡館饋餿饞饃餺餾饈饉饅饊饌饢馬馭馱馴馳驅駁驢駔駛駟駙駒駐騶駕驛駘驍罵驕驊駱駭駢驪驗駿騏騎騍騅驂騙騭騷騖驁騮騫騸驃騾驄驏驟驥驦驤髏髖髕鬢魘魎魚魷魯魴鮁鮃鯰鱸鮒鮑鱟鮐鮭鮚鮪鮞鱭鮫鮮鯗鱘鯁鱺鰱鰹鯉鰣鰷鯀鯊鯇鯽鯖鯪鯫鯡鯤鯧鯝鯢鯰鯛鯨鯔鱝鰈鱷鰍鰒鰉鰲鰭鰨鰥鰩鰳鰾鱈鱉鰻鰵鱅鱖鱔鱗鱒鱤鱧鳥鳩雞鳶鳴鷗鴉鴇鴆鴣鶇鸕鴨鴦鴟鴝鴛鴕鷥鷙鴯鴰鵂鴿鸞鴻鵓鸝鵑鵠鵝鵒鷳鵜鵡鵲鶓鵪鵬鵮鶉鶊鶘鶚鶻鶿鶥鶩鷂鶼鶴鸚鷓鷚鷯鷦鷲鷸鷺鷹鸌鸏鸛麥麩黃黌黶黷黲黽黿鼉鞀鼴齊齏齒齔齟齡齙齠齜齦齬齪齷龍龔龕龜")
    return sum(text.count(char) for char in simplified_chars) - sum(text.count(char) for char in traditional_chars)


def _parse_request_asset_name(name: str) -> dict[str, Any] | None:
    match = re.match(
        r"^Kizuna_(?P<string_id>[^_]+)_LV(?P<level>\d+)_Request_(?P<kind>Ingredient|Beverage)",
        name,
    )
    if not match:
        return None
    kind_labels = {
        "Ingredient": "食材请求",
        "Beverage": "饮品请求",
    }
    kind = match.group("kind")
    return {
        "string_id": match.group("string_id"),
        "level": int(match.group("level")),
        "kind": kind,
        "kind_label": kind_labels.get(kind, kind),
    }


def _id_map(rows: list[dict[str, str]], id_key: str = "id") -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        raw = row.get(id_key) or row.get(id_key.upper()) or row.get("ID")
        if raw is None:
            continue
        try:
            result[int(raw)] = row
        except ValueError:
            continue
    return result


def _extract_profiles(bundle_path: Path) -> dict[str, dict[str, Any]]:
    names = {
        "RecipeProfile",
        "IngredientProfile",
        "FoodProfile",
        "BeverageProfile",
        "CookerProfile",
        "FoodTagProfile",
        "BeverageTagProfile",
        "NormalGuestProfile",
        "SpecialGuestProfile",
        "IzakayaProfile",
        "DaySceneMapStatusProfile",
    }
    profiles: dict[str, dict[str, Any]] = {}
    for obj in _load_bundle(bundle_path).objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        name = str(tree.get("m_Name") or "")
        if name in names:
            profiles[name] = tree
    missing = sorted(names - profiles.keys())
    if missing:
        raise RuntimeError(f"缺少核心 profile：{missing}")
    return profiles


def _tag_names(ids: list[int], tag_map: dict[int, dict[str, str]]) -> list[str]:
    result: list[str] = []
    for tag_id in ids:
        row = tag_map.get(int(tag_id))
        if row:
            result.append(row.get("tag") or row.get("Tag") or str(tag_id))
        else:
            result.append(str(tag_id))
    return result


def _food_effective_tag_ids(row: dict[str, Any]) -> list[int]:
    tag_ids = [int(item) for item in row.get("tags", [])]
    base_value = float(row.get("baseValue", 0) or 0)
    if base_value < 20 and -2 not in tag_ids:
        tag_ids.insert(0, -2)
    elif base_value > 60 and -3 not in tag_ids:
        tag_ids.insert(0, -3)
    return tag_ids


def _weighted_tag_names(items: list[dict[str, Any]], tag_map: dict[int, dict[str, str]]) -> list[str]:
    result: list[str] = []
    for item in items:
        tag_id = int(item.get("tagId", 0))
        row = tag_map.get(tag_id)
        name = row.get("tag") if row else str(tag_id)
        weight = item.get("weight", 1)
        result.append(f"{name} x{weight}" if weight != 1 else name)
    return result


def _weighted_tag_requests(
    items: list[dict[str, Any]],
    tag_map: dict[int, dict[str, str]],
    lines: list[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        tag_id = int(item.get("tagId", 0))
        row = tag_map.get(tag_id)
        result.append({
            "tag_id": tag_id,
            "tag": row.get("tag") if row else str(tag_id),
            "weight": item.get("weight", 1),
            "line": lines[index] if index < len(lines) else "",
        })
    for index, line in enumerate(lines[len(items):], start=len(items) + 1):
        result.append({
            "tag_id": None,
            "tag": "",
            "weight": 1,
            "line": line,
            "line_index": index,
        })
    return result


MAP_LABELS: dict[str, str] = {
    "": "默认摊位",
    "BeastForest": "兽道",
    "HumanVillage": "人间之里",
    "HakureiShrine": "博丽神社",
    "ScarletMansion": "红魔馆",
    "BambooForest": "迷途竹林",
    "PartyStage": "宴会舞台",
    "Hakugyokurou": "白玉楼",
    "Home": "家",
    "Basement": "地下室",
}

SPECIAL_GUEST_ICON_FALLBACKS: dict[str, list[str]] = {
    "Marisa": ["Marisa-Pic"],
    "Kyouko": ["@佐仓kyouko"],
    "Yuyuko": ["Boss_Yuyuko", "Skill_Yuyuko_Reward"],
    "Yuyuko_Free": ["Boss_Yuyuko", "Skill_Yuyuko_Reward"],
    "Youmu": ["Skill_Youmu_Reward", "Skill_Youmu_Punishment"],
    "Youmu_Free": ["Skill_Youmu_Reward", "Skill_Youmu_Punishment"],
    "ThreeFairies": ["Trinity"],
    "Ushio": ["Skill_Ushio_Punishment"],
    "Yuu": ["Skill_Yuu_Reward", "Skill_Yuu_Punishment"],
    "TBS_Kokoro": ["Kokoro", "Kokoro_Negative"],
    "Rinnosuke": ["霖之助Main_1, 0"],
    "Kosuzu": ["小铃Main_0, 1"],
    "Aunn": ["阿吽Main_0, 1", "阿吽 笑"],
    "Sakuya": ["咲夜Main_0, 0", "十六夜咲夜_Default"],
    "Reisen": ["铃仙Main_0, 0", "铃仙·优昙华院_Default"],
    "Remilia": ["蕾米Main_0, 1", "蕾米_Default"],
    "MengChengGuo": ["萌澄果Main_0, 0"],
    "Mystia_Current": ["米斯蒂娅 普通（微笑）"],
    "Kyouko_Current": ["响子店员 普通", "响子 普通"],
    "Rin": ["冴月麟Main_0, 0", "麟", "Rinny-tan_0"],
}

SPECIAL_GUEST_ICON_SPRITE_NAMES = {
    name
    for names in SPECIAL_GUEST_ICON_FALLBACKS.values()
    for name in names
}

SPECIAL_GUEST_IMAGE_PREFIXES: dict[str, list[str]] = {
    "Wriggle": ["莉格露"],
    "Rumia": ["露米娅"],
    "Chen": ["橙"],
    "Akyuu": ["阿求", "稗田阿求"],
    "Keine": ["慧音", "上白泽慧音"],
    "Kasen": ["华扇", "茨木华扇"],
    "Rinnosuke": ["霖之助"],
    "Reimu": ["灵梦", "博丽灵梦"],
    "Suika": ["萃香"],
    "Tenshi": ["天子"],
    "Marisa": ["魔理沙"],
    "Kyouko": ["响子", "幽谷响子"],
    "Kosuzu": ["小铃"],
    "Aunn": ["阿吽"],
    "Sakuya": ["咲夜", "十六夜咲夜"],
    "Reisen": ["铃仙", "铃仙·优昙华院"],
    "Meirin": ["美铃", "红美铃"],
    "Cirno": ["琪露诺"],
    "Patchouli": ["帕秋莉"],
    "Mokou": ["妹红", "藤原妹红"],
    "Kaguya": ["辉夜", "蓬莱山辉夜"],
    "Tewi": ["帝", "因幡帝"],
    "Remilia": ["蕾米", "蕾米莉亚"],
    "Yuyuko": ["幽幽子", "西行寺幽幽子"],
    "Yuyuko_Free": ["幽幽子", "西行寺幽幽子"],
    "Youmu": ["妖梦", "魂魄妖梦"],
    "Youmu_Free": ["妖梦", "魂魄妖梦"],
    "MengChengGuo": ["萌澄果"],
    "ThreeFairies": ["三妖精", "蹦蹦跳跳的三妖精"],
    "Mystia_Current": ["米斯蒂娅"],
    "Kyouko_Current": ["响子", "幽谷响子"],
    "Rin": ["麟", "冴月麟"],
    "Ushio": ["立空汐", "Ushio"],
    "Yuu": ["时焉侑", "Yuu"],
    "TBS_Kokoro": ["秦心", "Kokoro"],
}


def _format_probability(value: float) -> str:
    return f"{value * 100:.2f}%"


def _range_record(value: dict[str, Any] | None) -> dict[str, Any]:
    value = value or {}
    return {
        "min": value.get("x"),
        "max": value.get("y"),
    }


def _format_number(value: Any) -> str:
    number = float(value)
    return f"{number:g}"


def _range_summary(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    min_value = value.get("min")
    max_value = value.get("max")
    if min_value in (None, "") and max_value in (None, ""):
        return ""
    return f"{_format_number(min_value)}-{_format_number(max_value)}秒"


def _location_variant_summary(location: dict[str, Any]) -> str:
    parts: list[str] = []
    interval = _range_summary(location.get("normal_guest_span_interval"))
    if interval and interval != "0-0秒":
        parts.append(f"客流 {interval}")
    special_names = [
        str(guest.get("name"))
        for guest in location.get("special_guests", [])
        if guest.get("name") and not guest.get("only_spawn_after_unlocking")
    ][:3]
    if special_names:
        parts.append(f"稀客 {'、'.join(special_names)}")
    return "；".join(parts)


def _special_guest_icon(image_by_name: dict[str, dict[str, Any]], string_id: str) -> dict[str, Any] | None:
    candidates = [string_id]
    if "_" in string_id:
        candidates.append(string_id.split("_", 1)[0])
    for candidate in candidates:
        image = image_by_name.get(f"Flex_{candidate}")
        if image:
            return image
    for name in SPECIAL_GUEST_ICON_FALLBACKS.get(string_id, []):
        image = image_by_name.get(name)
        if image:
            return image
    return None


def _portrait_score(name: str) -> tuple[int, str]:
    if "Main_" in name:
        return (0, name)
    if name.startswith("Flex_"):
        return (1, name)
    if "Default" in name or "普通" in name or "微笑" in name:
        return (2, name)
    if name.startswith("Skill_"):
        return (4, name)
    return (3, name)


def _special_guest_portraits(image_by_name: dict[str, dict[str, Any]], string_id: str) -> list[dict[str, Any]]:
    names: set[str] = set()
    candidates = [string_id]
    if "_" in string_id:
        candidates.append(string_id.split("_", 1)[0])
    for candidate in candidates:
        names.add(f"Flex_{candidate}")
    names.update(SPECIAL_GUEST_ICON_FALLBACKS.get(string_id, []))
    prefixes = SPECIAL_GUEST_IMAGE_PREFIXES.get(string_id, [])
    for image_name in image_by_name:
        if image_name in names:
            continue
        for prefix in prefixes:
            if (
                image_name == prefix
                or image_name.startswith(f"{prefix} ")
                or image_name.startswith(f"{prefix}_")
                or image_name.startswith(f"{prefix}Main_")
            ):
                names.add(image_name)
                break
    records = [
        image_by_name[name]
        for name in sorted(names, key=_portrait_score)
        if image_by_name.get(name)
    ]
    non_main_records = [
        record for record in records
        if "Main_" not in str(record.get("name", ""))
    ]
    if non_main_records:
        records = non_main_records
    deduped: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for record in records:
        path = str(record.get("path"))
        if path in seen_paths:
            continue
        deduped.append(record)
        seen_paths.add(path)
    return deduped


def _asset_url(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    return f"/api/mystia/asset/{normalized}"


def _image_record(name: str, kind: str, bundle: Path, relative_path: str, size: tuple[int, int]) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "bundle": bundle.name,
        "path": relative_path.replace("\\", "/"),
        "url": _asset_url(relative_path),
        "width": size[0],
        "height": size[1],
    }


def _export_sprite_image(sprite: Any, kind: str, bundle: Path, output_root: Path) -> dict[str, Any] | None:
    try:
        name = str(sprite.m_Name)
        image = sprite.image
    except Exception:
        return None
    if image is None:
        return None
    filename = f"{kind}__{_safe_asset_stem(name)}.png"
    relative_path = f"{ASSET_IMAGE_DIR}/{filename}"
    target = output_root / relative_path
    image.save(target)
    return _image_record(name, kind, bundle, relative_path, image.size)


def _audio_group(name: str) -> str:
    lowered = name.lower()
    if "loop" in lowered or "intro" in lowered or lowered in {"op_intro", "op_loop"}:
        return "bgm"
    if lowered.startswith("spell_"):
        return "spell"
    if lowered.startswith("ui_"):
        return "ui"
    if lowered.startswith("sfx_") or lowered.startswith("common_"):
        return "sfx"
    if "_" in name and name.split("_", 1)[0][:1].isupper():
        return "character"
    return "other"


def _parse_named_id(name: str, prefix: str) -> int | None:
    match = re.fullmatch(rf"{re.escape(prefix)}_(\d+)", name)
    if not match:
        return None
    return int(match.group(1))


def _normal_guest_sprite_id(name: str) -> int | None:
    match = re.match(r"^(\d+)_.*_body_", name)
    if not match:
        return None
    guest_id = int(match.group(1))
    return guest_id if 0 <= guest_id <= 14 else None


def _normal_guest_sprite_score(name: str) -> tuple[int, str]:
    frame_match = re.search(r"_(\d+),\s*(\d+)$", name)
    frame_score = 1
    if frame_match and frame_match.group(1) == "0" and frame_match.group(2) == "0":
        frame_score = 0
    return (frame_score, name)


def _is_special_guest_portrait_sprite(name: str) -> bool:
    if name in SPECIAL_GUEST_ICON_SPRITE_NAMES:
        return True
    if name.startswith("Flex_") and not name.startswith("Flex_BG"):
        return True
    if name.startswith("Skill_"):
        return True
    if "Eyes_" in name or "Hair_" in name or "Dress_" in name or "Back_" in name:
        return False
    for prefixes in SPECIAL_GUEST_IMAGE_PREFIXES.values():
        for prefix in prefixes:
            if name == prefix or name.startswith(f"{prefix} ") or name.startswith(f"{prefix}_") or name.startswith(f"{prefix}Main_"):
                return True
    return False


def _scan_asset_bundles(bundle_root: Path, output_root: Path) -> dict[str, Any]:
    bundle_paths = sorted(bundle_root.glob("*.bundle"))
    sprite_bundles: list[Path] = []
    audio_bundles: list[Path] = []
    image_candidates: dict[str, list[tuple[Path, str]]] = {
        "food_icons": [],
        "food_plates": [],
        "ingredient_icons": [],
        "beverage_icons": [],
        "character_sprites": [],
        "special_guest_portraits": [],
        "normal_guest_icons": [],
    }
    normal_guest_candidates: dict[int, tuple[tuple[int, str], Path, str]] = {}
    request_texts: dict[str, dict[str, Any]] = {}

    for bundle_path in bundle_paths:
        has_audio = False
        has_target_sprite = False
        try:
            env = _load_bundle(bundle_path)
        except Exception:
            continue
        for obj in env.objects:
            if obj.type.name == "AudioClip":
                has_audio = True
                continue
            if obj.type.name == "TextAsset":
                try:
                    text_asset = obj.read()
                    text_name = str(text_asset.m_Name)
                    request_meta = _parse_request_asset_name(text_name)
                except Exception:
                    continue
                if request_meta:
                    lines = _parse_request_lines(str(text_asset.m_Script))
                    if lines:
                        zh_score = _zh_text_score(lines)
                        if zh_score <= 0:
                            continue
                        simplified_score = _simplified_zh_score(lines)
                        current = request_texts.get(text_name)
                        if current is None or (zh_score, simplified_score, len(lines)) > (
                            current["zh_score"],
                            current["simplified_score"],
                            len(current["lines"]),
                        ):
                            request_texts[text_name] = {
                                **request_meta,
                                "asset_name": text_name,
                                "bundle": bundle_path.name,
                                "zh_score": zh_score,
                                "simplified_score": simplified_score,
                                "lines": lines,
                            }
                continue
            if obj.type.name != "Sprite":
                continue
            try:
                sprite_name = str(obj.read().m_Name)
            except Exception:
                continue
            if _parse_named_id(sprite_name, "Foods") is not None:
                image_candidates["food_icons"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif _parse_named_id(sprite_name, "FoodPlates") is not None:
                image_candidates["food_plates"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif _parse_named_id(sprite_name, "Ingredients") is not None:
                image_candidates["ingredient_icons"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif _parse_named_id(sprite_name, "Beverages") is not None:
                image_candidates["beverage_icons"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif _is_special_guest_portrait_sprite(sprite_name):
                image_candidates["special_guest_portraits"].append((bundle_path, sprite_name))
                has_target_sprite = True
            elif _normal_guest_sprite_id(sprite_name) is not None:
                guest_id = _normal_guest_sprite_id(sprite_name)
                score = _normal_guest_sprite_score(sprite_name)
                current = normal_guest_candidates.get(guest_id)
                if current is None or score < current[0]:
                    normal_guest_candidates[guest_id] = (score, bundle_path, sprite_name)
                has_target_sprite = True
            elif any(token in sprite_name for token in ["Mystia", "Wriggle", "Rumia", "Keine", "Cirno", "Reisen"]):
                image_candidates["character_sprites"].append((bundle_path, sprite_name))
                has_target_sprite = True
        if has_target_sprite:
            sprite_bundles.append(bundle_path)
        if has_audio:
            audio_bundles.append(bundle_path)
    image_candidates["normal_guest_icons"].extend(
        (bundle_path, sprite_name)
        for _score, bundle_path, sprite_name in normal_guest_candidates.values()
    )

    related_sprite_paths = sorted(set(sprite_bundles + [
        path for path, _name in image_candidates["food_icons"]
        + image_candidates["food_plates"]
        + image_candidates["ingredient_icons"]
        + image_candidates["beverage_icons"]
        + image_candidates["character_sprites"]
        + image_candidates["special_guest_portraits"]
        + image_candidates["normal_guest_icons"]
    ]))
    image_root = output_root / ASSET_IMAGE_DIR
    image_root.mkdir(parents=True, exist_ok=True)

    images: list[dict[str, Any]] = []
    image_by_name: dict[str, dict[str, Any]] = {}
    exported_image_names: set[str] = set()
    if related_sprite_paths:
        env = _load_bundles(related_sprite_paths)
        wanted = {
            name: kind
            for kind, pairs in image_candidates.items()
            for _path, name in pairs
        }
        for obj in env.objects:
            if obj.type.name != "Sprite":
                continue
            try:
                sprite = obj.read()
                name = str(sprite.m_Name)
            except Exception:
                continue
            kind = wanted.get(name)
            if not kind:
                continue
            image_key = f"{kind}:{name}"
            if image_key in exported_image_names:
                continue
            record = _export_sprite_image(sprite, kind, Path(getattr(obj.assets_file, "path", "")), output_root)
            if record is None:
                continue
            images.append(record)
            image_by_name[name] = record
            exported_image_names.add(image_key)

    audio_root = output_root / ASSET_AUDIO_DIR
    audio_root.mkdir(parents=True, exist_ok=True)
    audio: list[dict[str, Any]] = []
    for bundle_path in audio_bundles:
        try:
            env = _load_bundle(bundle_path)
        except Exception:
            continue
        for obj in env.objects:
            if obj.type.name != "AudioClip":
                continue
            try:
                clip = obj.read()
                clip_name = str(clip.m_Name)
                samples = dict(clip.samples)
            except Exception:
                continue
            for sample_name, sample_bytes in samples.items():
                suffix = Path(sample_name).suffix or ".wav"
                filename = f"{_safe_asset_stem(clip_name)}{suffix}"
                relative_path = f"{ASSET_AUDIO_DIR}/{filename}"
                target = output_root / relative_path
                target.write_bytes(sample_bytes)
                audio.append({
                    "name": clip_name,
                    "group": _audio_group(clip_name),
                    "bundle": bundle_path.name,
                    "path": relative_path,
                    "url": _asset_url(relative_path),
                    "bytes": len(sample_bytes),
                    "format": suffix.lstrip(".").lower(),
                })

    return {
        "images": sorted(images, key=lambda item: (item["kind"], item["name"])),
        "audio": sorted(audio, key=lambda item: (item["group"], item["name"])),
        "special_guest_requests": sorted(
            request_texts.values(),
            key=lambda item: (item["string_id"], item["level"], item["kind"], item["asset_name"]),
        ),
        "image_by_name": image_by_name,
        "asset_stats": {
            "image_count": len(images),
            "audio_count": len(audio),
            "sprite_bundle_count": len(sprite_bundles),
            "audio_bundle_count": len(audio_bundles),
        },
    }


def _extract_cooker_icon_assets(
    profile_bundle: Path,
    output_root: Path,
    cookers_lang: dict[int, dict[str, str]],
) -> dict[str, Any]:
    env = _load_bundle(profile_bundle)
    records: list[dict[str, Any]] = []
    cooker_by_id: dict[int, dict[str, Any]] = {}
    cooker_by_type: dict[int, dict[str, Any]] = {}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            profile = obj.read()
        except Exception:
            continue
        if getattr(profile, "m_Name", "") != "CookerProfile":
            continue
        for row in profile.cookers:
            cooker_id = int(getattr(row, "id", -999))
            cooker_series = int(getattr(row, "cookerSeries", -1))
            if cooker_id < 0 or cooker_series != 0:
                continue
            try:
                idle = row.idle.read()
                sprite = idle.m_AnimatedSprites[0].read()
            except Exception:
                sprite = None
            icon = _export_sprite_image(sprite, "cooker_icons", profile_bundle, output_root) if sprite else None
            if icon is not None:
                records.append(icon)
            lang = cookers_lang.get(cooker_id, {})
            cooker = {
                "id": cooker_id,
                "type": int(getattr(row, "type", cooker_id)),
                "series": cooker_series,
                "name": lang.get("NAME") or lang.get("Name") or str(cooker_id),
                "assets": {"icon": icon},
            }
            cooker_by_id[cooker_id] = cooker
            cooker_by_type[cooker["type"]] = cooker
        break
    return {
        "images": records,
        "cooker_by_id": cooker_by_id,
        "cooker_by_type": cooker_by_type,
    }


def build_catalog(game_root: Path, output_root: Path) -> dict[str, Any]:
    bundle_root = game_root / AA_DIR
    profile_bundle = bundle_root / CORE_PROFILE_BUNDLE
    lang_bundle = bundle_root / SIMPLIFIED_LANG_BUNDLE
    if not profile_bundle.exists():
        raise FileNotFoundError(profile_bundle)
    if not lang_bundle.exists():
        raise FileNotFoundError(lang_bundle)

    profiles = _extract_profiles(profile_bundle)
    texts = _read_named_text_assets(lang_bundle)
    asset_catalog = _scan_asset_bundles(bundle_root, output_root)
    image_by_name = asset_catalog["image_by_name"]

    foods_lang = _id_map(_parse_tsv(texts["FoodsLang"]))
    ingredients_lang = _id_map(_parse_tsv(texts["IngredientsLang"]))
    beverages_lang = _id_map(_parse_tsv(texts["BeveragesLang"]))
    food_tags = _id_map(_parse_tsv(texts["FoodTagsLang"]))
    beverage_tags = _id_map(_parse_tsv(texts["BeverageTagsLang"]))
    cookers_lang = _id_map(_parse_tsv(texts["CookersLang"]), "ID")
    normal_guest_lang = _id_map(_parse_tsv(texts["NormGuestLang"]), "ID")
    special_guest_lang = _id_map(_parse_tsv(texts["SpecGuestLang"]), "ID")
    special_guest_spells = _parse_spell_rows(texts["SpellLang"])
    special_guest_conversations = _parse_special_conversations(texts["SpecConv"])
    special_guest_evaluations = _parse_special_evaluations(texts["SpecEvalLang"])
    special_guest_food_tag_lines = _parse_special_tag_lines(texts["SpecFoodTagLang"])
    special_guest_beverage_tag_lines = _parse_special_tag_lines(texts["SpecBevTagLang"])
    special_guest_requests: dict[str, list[dict[str, Any]]] = {}
    for request in asset_catalog.get("special_guest_requests", []):
        special_guest_requests.setdefault(str(request["string_id"]), []).append(request)
    cooker_assets = _extract_cooker_icon_assets(profile_bundle, output_root, cookers_lang)
    asset_catalog["images"].extend(cooker_assets["images"])
    asset_catalog["images"] = sorted(asset_catalog["images"], key=lambda item: (item["kind"], item["name"]))
    asset_catalog["asset_stats"]["image_count"] = len(asset_catalog["images"])
    image_by_name = {
        item["name"]: item
        for item in asset_catalog["images"]
    }
    normal_guest_icon_by_id = {
        int(match.group(1)): item
        for item in asset_catalog["images"]
        if item.get("kind") == "normal_guest_icons"
        for match in [re.match(r"^(\d+)_", str(item.get("name", "")))]
        if match
    }

    foods: list[dict[str, Any]] = []
    for row in profiles["FoodProfile"]["sellables"]:
        food_id = int(row["id"])
        lang = foods_lang.get(food_id, {})
        tags = [int(item) for item in row.get("tags", [])]
        effective_tag_ids = _food_effective_tag_ids(row)
        foods.append({
            "id": food_id,
            "name": lang.get("name", str(food_id)),
            "description": lang.get("Description", ""),
            "level": row.get("level"),
            "base_value": row.get("baseValue"),
            "tag_ids": tags,
            "tags": _tag_names(tags, food_tags),
            "effective_tag_ids": effective_tag_ids,
            "effective_tags": _tag_names(effective_tag_ids, food_tags),
            "ban_tag_ids": row.get("banTags", []),
            "ban_tags": _tag_names([int(item) for item in row.get("banTags", [])], food_tags),
            "is_collab": bool(row.get("isCollab")),
            "assets": {
                "icon": image_by_name.get(f"Foods_{food_id}"),
                "plate": image_by_name.get(f"FoodPlates_{food_id}"),
            },
        })

    ingredients: list[dict[str, Any]] = []
    for row in profiles["IngredientProfile"]["ingredients"]:
        ingredient_id = int(row["id"])
        lang = ingredients_lang.get(ingredient_id, {})
        tags = [int(item) for item in row.get("tags", [])]
        ingredients.append({
            "id": ingredient_id,
            "name": lang.get("name", str(ingredient_id)),
            "description": lang.get("Description", ""),
            "level": row.get("level"),
            "base_value": row.get("baseValue"),
            "tag_ids": tags,
            "tags": _tag_names(tags, food_tags),
            "prefix": row.get("prefix"),
            "assets": {
                "icon": image_by_name.get(f"Ingredients_{ingredient_id}"),
            },
        })

    beverages: list[dict[str, Any]] = []
    for row in profiles["BeverageProfile"]["sellables"]:
        beverage_id = int(row["id"])
        lang = beverages_lang.get(beverage_id, {})
        tags = [int(item) for item in row.get("tags", [])]
        beverages.append({
            "id": beverage_id,
            "name": lang.get("name", str(beverage_id)),
            "description": lang.get("Description", ""),
            "level": row.get("level"),
            "base_value": row.get("baseValue"),
            "tag_ids": tags,
            "tags": _tag_names(tags, beverage_tags),
            "ban_tag_ids": row.get("banTags", []),
            "ban_tags": _tag_names([int(item) for item in row.get("banTags", [])], beverage_tags),
            "is_collab": bool(row.get("isCollab")),
            "assets": {
                "icon": image_by_name.get(f"Beverages_{beverage_id}"),
            },
        })

    ingredient_by_id = {item["id"]: item for item in ingredients}
    food_by_id = {item["id"]: item for item in foods}
    cooker_by_type = {
        int(row["type"]): row
        for row in profiles["CookerProfile"]["cookers"]
        if int(row.get("id", -999)) >= 0 and int(row.get("cookerSeries", -1)) == 0
    }
    recipes: list[dict[str, Any]] = []
    for row in profiles["RecipeProfile"]["recipes"]:
        food = food_by_id.get(int(row["foodID"]), {})
        cooker_type = int(row["cookerType"])
        cooker_id = int(cooker_by_type.get(cooker_type, {}).get("id", cooker_type))
        cooker_lang = cookers_lang.get(cooker_id, {})
        cooker_asset = cooker_assets["cooker_by_id"].get(cooker_id) or cooker_assets["cooker_by_type"].get(cooker_type)
        recipe_ingredients = [
            ingredient_by_id.get(int(ingredient_id), {"id": int(ingredient_id), "name": str(ingredient_id)})
            for ingredient_id in row.get("ingredients", [])
        ]
        recipes.append({
            "id": int(row["id"]),
            "food_id": int(row["foodID"]),
            "food_name": food.get("name", str(row["foodID"])),
            "ingredients": [{"id": item["id"], "name": item["name"]} for item in recipe_ingredients],
            "cooker_type": cooker_type,
            "cooker_name": cooker_lang.get("NAME") or cooker_lang.get("Name") or str(cooker_type),
            "cooker": cooker_asset,
            "cook_time": row.get("cookTime"),
            "food_tags": food.get("tags", []),
            "food_base_value": food.get("base_value"),
        })
    recipe_by_food_id = {item["food_id"]: item for item in recipes}
    merged_foods: list[dict[str, Any]] = []
    for food in foods:
        recipe = recipe_by_food_id.get(food["id"])
        merged = dict(food)
        if recipe:
            merged.update({
                "has_recipe": True,
                "recipe_id": recipe["id"],
                "ingredients": recipe["ingredients"],
                "cooker_type": recipe["cooker_type"],
                "cooker_name": recipe["cooker_name"],
                "cooker": recipe["cooker"],
                "cook_time": recipe["cook_time"],
            })
            merged.setdefault("assets", {})["cooker"] = recipe["cooker"].get("assets", {}).get("icon") if recipe.get("cooker") else None
        else:
            merged["has_recipe"] = False
        merged_foods.append(merged)

    guests: list[dict[str, Any]] = []
    for row in profiles["NormalGuestProfile"]["normalGuests"]:
        guest_id = int(row["id"])
        lang = normal_guest_lang.get(guest_id, {})
        guests.append({
            "id": guest_id,
            "name": lang.get("NAME") or lang.get("Name") or str(guest_id),
            "description": lang.get("Description") or lang.get("DESCRIPTION") or "",
            "fund_multiplier": row.get("fundMultiplier"),
            "evaluation": row.get("evaluation"),
            "like_food_tag_ids": row.get("likeFoodTag", []),
            "like_food_tags": _tag_names([int(item) for item in row.get("likeFoodTag", [])], food_tags),
            "like_beverage_tag_ids": row.get("likeBevTag", []),
            "like_beverage_tags": _tag_names([int(item) for item in row.get("likeBevTag", [])], beverage_tags),
            "is_child": bool(row.get("isChild")),
            "assets": {
                "icon": normal_guest_icon_by_id.get(guest_id),
            },
        })

    special_guest_records: list[dict[str, Any]] = []
    for row in profiles["SpecialGuestProfile"]["specialGuests"]:
        guest_id = int(row["id"])
        lang = special_guest_lang.get(guest_id, {})
        description_parts = [
            value for key, value in lang.items()
            if key.lower().startswith("description") and value and value.upper() != "N/A"
        ]
        description = "\n\n".join(description_parts)
        name = lang.get("NAME") or lang.get("Name") or str(guest_id)
        spawn_marker = str(row.get("destination", {}).get("spawnMarker") or "")
        hidden_in_album = bool(row.get("hideInAlbum"))
        is_placeholder = (
            "?" in name
            or (hidden_in_album and not description and not spawn_marker)
        )
        special_guest_records.append({
            "id": guest_id,
            "string_id": row.get("stringId"),
            "name": name,
            "description": description,
            "description_parts": description_parts,
            "spells": special_guest_spells.get(guest_id, []),
            "conversations": special_guest_conversations.get(guest_id, []),
            "evaluations": special_guest_evaluations.get(guest_id, {}),
            "requests": special_guest_requests.get(str(row.get("stringId") or ""), []),
            "fund_range": row.get("fundRange"),
            "endurance_limit": row.get("enduranceLimit"),
            "like_food_tags": _weighted_tag_names(row.get("likeFoodTag", []), food_tags),
            "food_tag_requests": _weighted_tag_requests(
                row.get("likeFoodTag", []),
                food_tags,
                special_guest_food_tag_lines.get(guest_id, []),
            ),
            "hate_food_tags": _tag_names([int(item) for item in row.get("hateFoodTag", [])], food_tags),
            "like_beverage_tags": _weighted_tag_names(row.get("likeBevTag", []), beverage_tags),
            "beverage_tag_requests": _weighted_tag_requests(
                row.get("likeBevTag", []),
                beverage_tags,
                special_guest_beverage_tag_lines.get(guest_id, []),
            ),
            "commission_area": row.get("commisionAreaLabel"),
            "spawn_marker": spawn_marker,
            "hidden_in_album": hidden_in_album,
            "hide_in_nx": bool(row.get("hideInNX")),
            "is_placeholder": is_placeholder,
            "is_collab_character": bool(row.get("isCollabCharacter")),
            "assets": {
                "icon": _special_guest_icon(image_by_name, str(row.get("stringId") or "")),
                "portraits": _special_guest_portraits(image_by_name, str(row.get("stringId") or "")),
            },
        })
    special_guests = [
        item for item in special_guest_records
        if not item["is_placeholder"]
    ]
    guest_by_id = {item["id"]: item for item in guests}
    special_guest_by_id = {item["id"]: item for item in special_guest_records}

    izakaya_meta_by_id: dict[int, dict[str, Any]] = {}
    for map_node in profiles["DaySceneMapStatusProfile"]["allMapNodes"]:
        map_name = str(map_node.get("mapName") or "")
        for level, field_name in [
            (1, "level1IzakayaId"),
            (2, "level2IzakayaId"),
            (3, "level3IzakayaId"),
        ]:
            for izakaya_id in map_node.get(field_name, []):
                izakaya_meta_by_id[int(izakaya_id)] = {
                    "map_name": map_name,
                    "map_label": MAP_LABELS.get(map_name, map_name or "默认摊位"),
                    "shop_level": level,
                }

    locations: list[dict[str, Any]] = []
    guest_locations: dict[int, list[dict[str, Any]]] = {}
    special_guest_locations: dict[int, list[dict[str, Any]]] = {}
    for row in profiles["IzakayaProfile"]["izakayas"]:
        location_id = int(row["id"])
        day_scene_label = str(row.get("daySceneMapLabel") or "")
        meta = izakaya_meta_by_id.get(location_id, {
            "map_name": day_scene_label,
            "map_label": MAP_LABELS.get(day_scene_label, day_scene_label or "默认摊位"),
            "shop_level": None,
        })
        normal_pool = row.get("normalGuestPool", [])
        total_weight = sum(float(item.get("weight", 0) or 0) for item in normal_pool)
        normal_guest_rows: list[dict[str, Any]] = []
        for guest_id in sorted({
            int(guest_id)
            for item in normal_pool
            for guest_id in item.get("data", [])
        }):
            matched_groups = [
                item for item in normal_pool
                if guest_id in [int(value) for value in item.get("data", [])]
            ]
            weight = sum(float(item.get("weight", 0) or 0) for item in matched_groups)
            probability = weight / total_weight if total_weight else 0
            guest = guest_by_id.get(guest_id, {})
            record = {
                "id": guest_id,
                "name": guest.get("name", str(guest_id)),
                "weight": weight,
                "probability": probability,
                "probability_text": _format_probability(probability),
                "groups": [
                    {
                        "guest_ids": [int(value) for value in item.get("data", [])],
                        "guest_names": [
                            guest_by_id.get(int(value), {}).get("name", str(value))
                            for value in item.get("data", [])
                        ],
                        "weight": item.get("weight"),
                    }
                    for item in matched_groups
                ],
                "assets": guest.get("assets", {}),
            }
            normal_guest_rows.append(record)
            guest_locations.setdefault(guest_id, []).append({
                "id": location_id,
                "name": f"{meta['map_label']} Lv{meta['shop_level']}" if meta.get("shop_level") else meta["map_label"],
                "map_name": meta["map_name"],
                "map_label": meta["map_label"],
                "shop_level": meta.get("shop_level"),
                "probability": probability,
                "probability_text": _format_probability(probability),
            })

        special_guest_rows: list[dict[str, Any]] = []
        for item in row.get("specialGuestPool", []):
            guest_id = int(item.get("groupId", -1))
            probability = float(item.get("probPerDraw", 0) or 0)
            guest = special_guest_by_id.get(guest_id, {})
            record = {
                "id": guest_id,
                "name": guest.get("name", str(guest_id)),
                "string_id": guest.get("string_id"),
                "probability": probability,
                "probability_text": _format_probability(probability),
                "only_spawn_after_unlocking": bool(item.get("onlySpawnAfterUnlocking")),
                "only_spawn_when_place_recorded": bool(item.get("onlySpawnWhenPlaceBeRecorded")),
                "assets": guest.get("assets", {}),
            }
            special_guest_rows.append(record)
            special_guest_locations.setdefault(guest_id, []).append({
                "id": location_id,
                "name": f"{meta['map_label']} Lv{meta['shop_level']}" if meta.get("shop_level") else meta["map_label"],
                "map_name": meta["map_name"],
                "map_label": meta["map_label"],
                "shop_level": meta.get("shop_level"),
                "probability": probability,
                "probability_text": _format_probability(probability),
                "only_spawn_after_unlocking": bool(item.get("onlySpawnAfterUnlocking")),
                "only_spawn_when_place_recorded": bool(item.get("onlySpawnWhenPlaceBeRecorded")),
            })

        shop_level = meta.get("shop_level")
        location_name = f"{meta['map_label']} Lv{shop_level}" if shop_level else meta["map_label"]
        locations.append({
            "id": location_id,
            "name": location_name,
            "map_name": meta["map_name"],
            "map_label": meta["map_label"],
            "shop_level": shop_level,
            "base_fund_range": _range_record(row.get("baseFundRange")),
            "normal_guest_span_interval": _range_record(row.get("normalGuestSpanInterval")),
            "spawn_passerby_guest": bool(row.get("spawnPasserbyGuest")),
            "passerby_guest_span_interval": _range_record(row.get("passerbyGuestSpanInterval")),
            "normal_guest_pool_weight": total_weight,
            "normal_guests": sorted(normal_guest_rows, key=lambda item: (-item["probability"], item["id"])),
            "special_guest_gacha_interval": row.get("specialGuestGachaInterval"),
            "special_guests": sorted(special_guest_rows, key=lambda item: (-item["probability"], item["id"])),
            "guest_table_count": row.get("guestTableCount"),
            "cook_table_count": row.get("cookTableCount"),
            "music_package_index": row.get("musicPackageIndex"),
            "music_package_override": bool(row.get("musicPackageOverride")),
        })
    locations = sorted(locations, key=lambda item: (item["map_label"], item["shop_level"] or 0, item["id"]))
    location_groups: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
    for location in locations:
        location_groups.setdefault((location["map_label"], location.get("shop_level")), []).append(location)
    for grouped_locations in location_groups.values():
        if len(grouped_locations) <= 1:
            continue
        for index, location in enumerate(grouped_locations, start=1):
            location["variant_index"] = index
            location["variant_count"] = len(grouped_locations)
            location["variant_label"] = f"配置{index}"
            location["variant_summary"] = _location_variant_summary(location)

    for guest in guests:
        guest["locations"] = sorted(
            guest_locations.get(guest["id"], []),
            key=lambda item: (item["map_label"], item["shop_level"] or 0, item["id"]),
        )
    for guest in special_guest_records:
        guest["locations"] = sorted(
            special_guest_locations.get(guest["id"], []),
            key=lambda item: (item["map_label"], item["shop_level"] or 0, item["id"]),
        )

    catalog = {
        "schema_version": CATALOG_VERSION,
        "source": {
            "game_root": str(game_root),
            "profile_bundle": str(profile_bundle),
            "lang_bundle": str(lang_bundle),
            "bundle_xor_key": XOR_KEY,
            "asset_root": str(output_root),
        },
        "stats": {
            "foods": len(merged_foods),
            "ingredients": len(ingredients),
            "beverages": len(beverages),
            "recipes": len(recipes),
            "guests": len(guests),
            "special_guests": len(special_guests),
            "special_guest_records": len(special_guest_records),
            "locations": len(locations),
            **asset_catalog["asset_stats"],
        },
        "foods": merged_foods,
        "ingredients": ingredients,
        "beverages": beverages,
        "recipes": recipes,
        "guests": guests,
        "special_guests": special_guests,
        "special_guest_records": special_guest_records,
        "locations": locations,
        "images": asset_catalog["images"],
        "audio": asset_catalog["audio"],
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "mystia_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Touhou Mystia Izakaya reverse catalog.")
    parser.add_argument("--game-root", type=Path, default=DEFAULT_GAME_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ANALYSIS_ROOT)
    args = parser.parse_args()
    catalog = build_catalog(args.game_root, args.output_root)
    print(json.dumps({"output": str(args.output_root / "mystia_catalog.json"), "stats": catalog["stats"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
