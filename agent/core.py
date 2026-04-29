import json
import re
from pathlib import Path

import requests
from agent.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from agent.tools.poi import search_poi, geocode, search_around, search_along_route, robust_geocode
from agent.tools.routing import walk_distance
from agent.tools.graph_planner import build_graph, shortest_path, _haversine

API_URL = f"{LLM_BASE_URL.rstrip('/')}/v1/messages"

# Leaflet 静态资源缓存（内联到 HTML 避免 CDN 不可达）
_STATIC = {}
def _static(name):
    if name not in _STATIC:
        p = Path(__file__).parent / "static" / name
        _STATIC[name] = p.read_text(encoding="utf-8")
    return _STATIC[name]

SYSTEM_PROMPT = """你是一个路线规划解说员。你会收到算法计算好的最优路径（含站点、每段距离和交通工具）。
你的任务是用通俗友好的语言解释这条路线，说明为什么这样走最好。
忠实于算法结果，不修改站点顺序或交通工具。"""


class AgentSession:
    def __init__(self):
        self.city = ""
        self.default_city = ""
        self.all_pois = []
        self.stop_names = []
        self.start_name = ""
        self.dest_name = ""
        self.distance_info = ""
        self.path_result = None
        self.nodes = []
        self.messages = []


def _progress(emoji: str, msg: str):
    """打印阶段进度（只在 CLI 可见，不影响返回值）"""
    print(f"  {emoji}  {msg}")


def _call_api(messages: list, system: str = None, max_tokens: int = 4096) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": LLM_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


# 全量中国地级市 + 常见县级市，覆盖 300+ 城市
_ALL_CITIES = [
    # 直辖市
    "北京", "上海", "天津", "重庆",
    # 华北
    "石家庄", "唐山", "秦皇岛", "邯郸", "保定", "张家口", "承德", "沧州", "廊坊", "衡水", "太原", "大同",
    "阳泉", "长治", "晋城", "朔州", "忻州", "吕梁", "晋中", "临汾", "运城", "呼和浩特", "包头", "乌海",
    "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布",
    # 东北
    "沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭",
    "朝阳", "葫芦岛", "长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城", "延边", "哈尔滨",
    "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化",
    # 华东
    "南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州",
    "宿迁", "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水",
    "合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州",
    "六安", "亳州", "池州", "宣城", "福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩",
    "宁德", "南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶",
    "济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂",
    "德州", "聊城", "滨州", "菏泽",
    # 华中
    "郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡",
    "南阳", "商丘", "信阳", "周口", "驻马店", "武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门",
    "孝感", "荆州", "黄冈", "咸宁", "随州", "长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德",
    "张家界", "益阳", "郴州", "永州", "怀化", "娄底",
    # 华南
    "广州", "深圳", "珠海", "汕头", "佛山", "韶关", "湛江", "肇庆", "江门", "茂名", "惠州", "梅州",
    "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮", "南宁", "柳州", "桂林",
    "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左", "海口",
    "三亚", "三沙",
    # 西南
    "成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "南充", "眉山",
    "宜宾", "广安", "达州", "雅安", "巴中", "资阳", "贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁",
    "昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "拉萨", "日喀则",
    # 西北
    "西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛", "兰州", "嘉峪关",
    "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南", "西宁", "银川",
    "乌鲁木齐", "克拉玛依", "吐鲁番", "哈密",
    # 港澳台
    "香港", "澳门", "台北", "新北", "桃园", "台中", "台南", "高雄",
    # 热门旅游县级市 / 目的地
    "大理", "丽江", "香格里拉", "腾冲", "瑞丽", "景洪", "凯里", "都江堰", "峨眉山", "武夷山", "黄山",
    "庐山", "张家界", "凤凰", "婺源", "平遥", "敦煌", "哈密", "喀什", "伊宁", "满洲里", "延吉",
]
_CITY_PATTERN = re.compile("|".join(re.escape(c) for c in _ALL_CITIES))


def _extract_city(user_input: str, default_city: str = "") -> str:
    # 方法1: 从全量城市列表中匹配
    match = _CITY_PATTERN.search(user_input)
    if match:
        return match.group(0)
    # 方法2: "XX市" 模式（极少漏网的城市大概率带"市"字）
    m = re.search(r"([一-鿿]{2,3})市", user_input)
    if m:
        return m.group(1)
    # 方法3: 默认城市兜底
    if default_city:
        return default_city
    return ""


def _parse_intent(user_input: str, city: str) -> dict:
    """LLM 解析用户输入 → 结构化出行意图"""
    prompt = (
        "从用户需求中提取出行信息，只输出 JSON。\n"
        f"城市：{city}\n"
        f"用户：{user_input}\n\n"
        '{"origin": "起点地名", "destination": "终点地名或空",'
        ' "keywords": "搜索关键词逗号分隔", "num_stops": 2}\n\n'
        "规则：\n"
        "- origin/destination 必须保留用户输入中的完整地名原文，禁止截断或缩写。\n"
        "  如「丈八四路地铁站」不能简化为「四路地铁站」。\n"
        "- num_stops: 把用户提到的数量加起来（如「2个餐厅+最后去1个夜景」→3）。精确提取数字，用户没说时填3。\n"
        "- keywords: 必须是具体可搜索词（美食、咖啡、火锅、景点），不要用「有特色的地方」这种模糊词或单字「吃」「喝」。\n"
        "- destination: 如果有明确的最终目的地就填，否则留空。\n"
        '只输出 JSON。'
    )
    try:
        data = _call_api(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个信息提取助手，输出 JSON。",
            max_tokens=200,
        )
        text = data["content"][0]["text"].strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            # 拒绝占位符（LLM 没有真正提取）
            if parsed.get("origin") in (None, "起点地名", "未指定"):
                parsed["origin"] = ""
            if parsed.get("keywords", "") in ("搜索关键词逗号分隔", ""):
                parsed["keywords"] = "美食,景点"
            if parsed.get("destination") == "终点地名或空":
                parsed["destination"] = ""
            if isinstance(parsed.get("num_stops"), int) and 0 < parsed["num_stops"] <= 10:
                pass
            else:
                parsed["num_stops"] = 3
            return parsed
    except Exception:
        pass
    return {"origin": "", "destination": "", "keywords": "美食,景点", "num_stops": 3}


def _search_corridor_pois(origin_name: str, dest_name: str, keywords: str, city: str,
                          origin_coords=None, dest_coords=None) -> list:
    """在起终点之间的走廊区域搜索 POI，可分别在搜索前做 geocode"""
    import time
    all_pois = []
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    # 关键词规范化：映射单字泛词到可搜索词
    _KW_NORMALIZE = {
        "吃": "美食", "好吃的": "美食", "喝": "咖啡,茶饮", "玩": "景点,公园",
        "逛": "商场,购物", "宵夜": "小吃,烧烤,火锅", "深夜": "小吃,烧烤,火锅",
        "打卡": "景点,网红", "拍照": "景点,网红",
    }
    normalized = []
    for k in kw_list:
        # 先精确匹配
        if k in _KW_NORMALIZE:
            normalized.extend(_KW_NORMALIZE[k].split(","))
        else:
            # 再尝试子串匹配（如 "小吃宵夜"含"宵夜"）
            found = False
            for key, val in _KW_NORMALIZE.items():
                if key in k:
                    normalized.extend(val.split(","))
                    found = True
                    break
            if not found:
                normalized.append(k)
    kw_list = list(dict.fromkeys(normalized))  # 去重保序
    if not kw_list:
        kw_list = ["美食", "景点"]

    if origin_coords and dest_coords:
        o_str = f"{origin_coords[1]},{origin_coords[0]}"
        d_str = f"{dest_coords[1]},{dest_coords[0]}"
        for kw in kw_list:
            result = search_along_route(o_str, d_str, kw, radius=2000, limit=10)
            pois = json.loads(result)
            if isinstance(pois, list):
                all_pois.extend(pois)
            time.sleep(0.05)
        # 同时在终点周边补搜，确保终点附近也有候选
        for kw in kw_list:
            result = search_around(d_str, kw, radius=500, limit=5)
            pois = json.loads(result)
            if isinstance(pois, list):
                all_pois.extend(pois)
            time.sleep(0.05)
    elif origin_coords:
        o_str = f"{origin_coords[1]},{origin_coords[0]}"
        # 无终点时扩大搜索范围，确保远近都有候选
        for kw in kw_list:
            result = search_around(o_str, kw, radius=5000, limit=10)
            pois = json.loads(result)
            if isinstance(pois, list):
                all_pois.extend(pois)
            time.sleep(0.05)
        for kw in kw_list:
            result = search_around(o_str, kw, radius=1500, limit=5)
            pois = json.loads(result)
            if isinstance(pois, list):
                all_pois.extend(pois)
            time.sleep(0.05)
    elif dest_coords:
        # 无起点有终点：以终点为中心搜索，做成环线
        d_str = f"{dest_coords[1]},{dest_coords[0]}"
        for kw in kw_list:
            result = search_around(d_str, kw, radius=5000, limit=10)
            pois = json.loads(result)
            if isinstance(pois, list):
                all_pois.extend(pois)
            time.sleep(0.05)
        for kw in kw_list:
            result = search_around(d_str, kw, radius=1500, limit=5)
            pois = json.loads(result)
            if isinstance(pois, list):
                all_pois.extend(pois)
            time.sleep(0.05)
    else:
        for kw in kw_list:
            result = search_poi(keywords=kw, location=city, limit=10)
            pois = json.loads(result)
            if isinstance(pois, list):
                all_pois.extend(pois)

    # 品类黑名单过滤（打印店、维修店等无关杂项）
    _BANNED = ["打印", "复印", "图文", "广告", "快印", "印刷", "维修", "洗车", "药店", "中介"]
    filtered = []
    for p in all_pois:
        cat = p.get("category", "") + p.get("type", "")
        if not any(b in cat for b in _BANNED):
            filtered.append(p)
    return filtered


def _gather_pois(city: str, user_input: str) -> list:
    """LLM 先分析需求 → 决定去哪个区域搜、搜什么、半径多大 → Python 精准执行"""
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Step 1: LLM 制定搜索策略
    strategy_prompt = (
        f"你是一个出行路线规划师。分析用户需求，制定 POI 搜索策略。\n\n"
        f"城市：{city}\n"
        f"用户需求：{user_input}\n\n"
        f"请输出搜索区域，每行一个区域，格式：\n"
        f"搜索中心 | 关键词1,关键词2 | 搜索半径(米)\n\n"
        f"规则：\n"
        f"- 如果用户提到具体地点（路名/地标/商圈），以那里为中心，半径 500~2000 米\n"
        f"- 如果用户想去多个地方（如「先逛钟楼再去大雁塔」），每个地方一个区域\n"
        f"- 如果是全城漫游（如「随便逛逛」），用城市名做中心，半径 5000 米\n"
        f"- 关键词从用户需求里提取：美食/景点/咖啡馆/烧烤/亲子/购物等\n\n"
        f"例子1：\"从西稍门出发，逛吃半天\"\n"
        f"西稍门 | 美食,餐厅 | 2000\n\n"
        f"例子2：\"先逛钟楼再去大雁塔\"\n"
        f"钟楼 | 景点,名胜古迹 | 1000\n"
        f"大雁塔 | 景点,美食 | 1500\n\n"
        f"只输出区域行，不要其他内容。"
    )

    try:
        data = _call_api(
            messages=[{"role": "user", "content": strategy_prompt}],
            system="你是一个路线规划助手，输出搜索区域。",
            max_tokens=200,
        )
        strategy_text = data["content"][0]["text"].strip()
    except Exception:
        strategy_text = ""

    _progress("🎯", f"分析出行意图，制定搜索策略")
    if strategy_text:
        for line in strategy_text.split("\n"):
            line = line.strip()
            if line:
                _progress("   →", f"{line}")
    else:
        _progress("   →", "策略分析未返回，使用全城兜底搜索")

    # Step 2: 解析策略，并行执行搜索
    all_pois = []
    fallback = True

    if strategy_text:
        _progress("🔍", f"开始搜索 POI")
        for line in strategy_text.split("\n"):
            line = line.strip()
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                continue

            center = parts[0]
            keywords = [kw.strip() for kw in parts[1].split(",") if kw.strip()]
            radius = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 2000

            # 每个区域只需 geocode 一次
            try:
                gc = geocode(center, city)
                gc_data = json.loads(gc)
                if "lng" in gc_data and "lat" in gc_data:
                    loc = f"{gc_data['lng']},{gc_data['lat']}"
                    fallback = False
                    use_around = True
                else:
                    use_around = False
            except Exception:
                use_around = False

            def _search(kw):
                try:
                    if use_around:
                        r = search_around(loc, kw, radius=min(radius, 5000), limit=10)
                    else:
                        r = search_poi(keywords=kw, location=city, limit=10)
                except Exception:
                    r = search_poi(keywords=kw, location=city, limit=10)
                return kw, r

            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(_search, kw) for kw in keywords[:2]]
                for f in as_completed(futures):
                    kw, result = f.result()
                    time.sleep(0.1)
                    pois = json.loads(result)
                    if isinstance(pois, list):
                        _progress("   →", f"「{center}」搜到 {len(pois)} 个「{kw}」相关 POI")
                        all_pois.extend(pois)

    # Step 3: 兜底——全城搜美食
    if fallback or len(all_pois) < 3:
        _progress("⚠️", f"搜索结果不足，全城兜底搜索中...")
        result = search_poi(keywords="美食", location=city, limit=10)
        pois = json.loads(result)
        if isinstance(pois, list):
            all_pois.extend(pois)

    # 去重
    seen = set()
    unique = []
    for p in all_pois:
        name = p.get("name", "")
        if name and name not in seen:
            seen.add(name)
            unique.append(p)

    _progress("✅", f"共获取 {len(unique)} 个不同 POI")

    # 提取首个策略区域作为起点名称
    first_center = ""
    if strategy_text:
        for line in strategy_text.split("\n"):
            line = line.strip()
            if "|" in line:
                first_center = line.split("|")[0].strip()
                break

    return unique, first_center


def _select_stops(pois: list, user_input: str, city: str, start_name: str = "") -> list:
    """让 LLM 从 POI 中选择 3-4 个路线站点，返回按顺序的名称列表（失败返回空列表）"""
    if not pois:
        return []

    # 按评分排序取 TOP 20，确保各区域都有代表
    sorted_pois = sorted(pois, key=lambda p: p.get("rating") or 0, reverse=True)
    top = sorted_pois[:20]
    summary = "\n".join(
        f"{i+1}. {p['name']} | {p.get('category','?')} | 评分:{p.get('rating','?')} | 人均:¥{p.get('price_per_person','?')}"
        for i, p in enumerate(top)
    )

    prompt = (
        f"为以下出行选择 3-4 个最合适的站点，按访问顺序排列。\n"
        f"城市：{city}  用户需求：{user_input}\n\n"
        f"可选 POI：\n{summary}\n\n"
        f"规则：\n"
        f"- 必须从上面列表中选，不要编造\n"
        f"- 路线应是前进方向，不要来回折返\n"
        f"- 避免选择重复或相似的站点（如「北京南站」和「北京南站(北出站口)」是同一个地方，只选一个）\n"
        f"- 不要选交通枢纽（火车站/机场/地铁站）作为站点\n"
        f"- 偏好评分高、人均适中的 POI\n\n"
        f"只输出站点名称，用 | 分隔。格式：站点1|站点2|站点3"
    )

    try:
        data = _call_api(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个路线规划助手，从给定 POI 中选出最合适的站点。",
            max_tokens=200,
        )
        text = data["content"][0]["text"]
        names = [n.strip() for n in text.split("|") if n.strip()]

        # 模糊匹配 POI 名称
        valid = {p["name"] for p in pois}
        matched = []
        for n in names:
            for v in valid:
                if n == v or n in v or v in n:
                    matched.append(v)
                    break
        return matched[:5]
    except Exception:
        return []


def _calc_route_distances(stop_names: list, pois: list, user_input: str, city: str, start_name: str = "") -> str:
    """计算站点间步行距离，返回可读的距离信息字符串"""
    if len(stop_names) < 2:
        return ""

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 构建 POI 名称 → 坐标 映射
    name_map = {}
    for p in pois:
        lng, lat = p.get("lng"), p.get("lat")
        if lng is not None and lat is not None:
            name_map[p["name"]] = f"{lng},{lat}"

    def _find_coords(name):
        if name in name_map:
            return name_map[name]
        for n, c in name_map.items():
            if name in n or n in name:
                return c
        return None

    # 用 LLM 策略解析的起点名称 geocode，不再用 regex 从原文硬抠
    start_point = None
    if start_name:
        try:
            gc = geocode(start_name, city)
            data = json.loads(gc)
            if "lng" in data and "lat" in data:
                start_point = f"{data['lng']},{data['lat']}"
        except Exception:
            pass

    # 收集所有需要计算的段
    tasks = []
    if start_point:
        coords = _find_coords(stop_names[0])
        if coords:
            tasks.append(("起点", stop_names[0], start_point, coords))

    for i in range(len(stop_names) - 1):
        a = _find_coords(stop_names[i])
        b = _find_coords(stop_names[i + 1])
        if a and b:
            tasks.append((stop_names[i], stop_names[i + 1], a, b))

    # 并行请求步行距离
    segments = [None] * len(tasks)

    def _calc(i, fr, to, src, dst):
        result = walk_distance(src, dst)
        if result:
            m, d = round(result["duration"] / 60), result["distance"]
            suffix = ""
            if m > 60:
                suffix = "（距离较远，建议地铁或打车）"
            elif m > 30:
                suffix = "（步行较远，建议骑行或打车）"
            return i, f"{fr}→{to}：步行约{m}分钟（{d}米）{suffix}"
        return i, None

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_calc, i, fr, to, src, dst) for i, (fr, to, src, dst) in enumerate(tasks)]
        for f in as_completed(futures):
            i, text = f.result()
            segments[i] = text

    return "\n".join(s for s in segments if s)


# ── Mermaid 美化辅助函数 ─────────────────────────────


def _shorten_name(name: str) -> str:
    """截断 POI 名称：去掉括号和副标题"""
    for sep in ["(", "（", "·", "—", "-"]:
        if sep in name:
            name = name.split(sep)[0]
    return name.strip()


def _emoji_for_poi(name: str, category: str = "") -> str:
    """根据 POI 名称和类别选择 emoji"""
    text = name + " " + category
    if any(w in text for w in ["咖啡", "café", "coffee"]):
        return "☕"
    if any(w in text for w in ["甜品", "面包", "蛋糕", "西饼", "烘焙"]):
        return "🍰"
    if any(w in text for w in ["火锅", "串串", "冒菜", "麻辣烫", "涮肉"]):
        return "🍲"
    if any(w in text for w in ["烧烤", "烤肉", "烤鸭", "烧腊"]):
        return "🍖"
    if any(w in text for w in ["海鲜", "生鲜"]):
        return "🦐"
    if any(w in text for w in ["日料", "日式", "寿司", "刺身", "居酒屋"]):
        return "🍣"
    if any(w in text for w in ["pizza", "披萨", "意面", "西餐", "牛排", "汉堡"]):
        return "🍕"
    if any(w in text for w in ["面", "粉", "饺子", "馄饨", "面馆"]):
        return "🍜"
    if any(w in text for w in ["奶茶", "茶饮", "茶", "饮品"]):
        return "🧋"
    if any(w in text for w in ["酒吧", "啤酒", "酒馆"]):
        return "🍸"
    if any(w in text for w in ["景点", "景区", "公园", "博物馆", "城墙", "古迹"]):
        return "🎡"
    if any(w in text for w in ["购物", "商场", "广场", "商业街"]):
        return "🛍️"
    if any(w in text for w in ["粥", "汤", "养生", "清淡"]):
        return "🥣"
    if any(w in text for w in ["图书馆", "书店", "书城"]):
        return "📖"
    if any(w in text for w in ["电影院", "影院", "剧院"]):
        return "🎬"
    return "🍽️"


def _short_dist(info: str) -> tuple:
    """把"步行约154分钟（11583米）（建议地铁或打车）" → ("🚕 154分", True)"""
    if not info:
        return "", False
    has_suggestion = "建议" in info
    emoji = "🚕" if has_suggestion else "🚶"
    m = re.search(r"(\d+)分钟", info)
    if m:
        mins = int(m.group(1))
        if mins == 0:
            d = re.search(r"（(\d+)米）", info)
            label = f"{emoji} {d.group(1)}m" if d else f"{emoji} <1分"
            return label, has_suggestion
        return f"{emoji} {mins}分", has_suggestion
    label = info.replace("步行约", f"{emoji} ").replace("（", "(").replace("）", ")")
    return label, has_suggestion


def _build_mermaid(stop_names: list, distance_info: str, user_input: str, start_name: str = "") -> str:
    """生成美化版 Mermaid 路线图（彩色节点 + emoji + 横向布局）"""
    if len(stop_names) < 2 or not distance_info:
        return ""

    # 用 LLM 策略解析的起点名称（来自 _gather_pois 的策略中心），不再用 regex
    if not start_name:
        start_name = "起点"

    # 解析距离信息
    seg_map = {}
    for line in distance_info.strip().split("\n"):
        if "：" in line:
            k, v = line.split("：", 1)
            seg_map[k] = v.strip()

    lines = [
        "flowchart LR",
        "    classDef start fill:#10b981,color:#fff,stroke:#059669,stroke-width:2px",
        "    classDef mid fill:#3b82f6,color:#fff,stroke:#2563eb,stroke-width:2px",
        "    classDef end fill:#f59e0b,color:#fff,stroke:#d97706,stroke-width:2px",
        "    classDef ride fill:#f97316,color:#fff,stroke:#ea580c,stroke-width:2px,stroke-dasharray:5,5",
    ]

    # 起点节点
    lines.append(f'    N0(["{start_name}"]):::start')

    # 各站点
    for i, name in enumerate(stop_names):
        style = "end" if i == len(stop_names) - 1 else "mid"
        short = _shorten_name(name)
        emoji = "🏁" if style == "end" else _emoji_for_poi(short)
        lines.append(f'    N{i+1}["{emoji} {short}"]:::{style}')

        # 从上一节点到当前节点的连线
        if i == 0:
            info = seg_map.get(f"起点→{name}", "")
        else:
            info = seg_map.get(f"{stop_names[i-1]}→{name}", "")

        dist, need_ride = _short_dist(info)
        if need_ride:
            arrow = f' ==|"{dist}"|==>'
            lines.append(f'    N{i}{arrow} N{i+1}')
            lines.append(f'    class N{i+1} ride;')
        else:
            arrow = f' -->|"{dist}"|' if dist else " -->"
            lines.append(f'    N{i}{arrow} N{i+1}')

    return "\n".join(lines)


_TRANSPORT_EMOJI = {"步行": "🚶", "骑行": "🚲", "公交/地铁": "🚌", "打车": "🚕"}
_TRANSPORT_CLASS = {"步行": "mid", "骑行": "ride", "公交/地铁": "transit", "打车": "ride"}


def _build_mermaid_from_path(start_name: str, path_result: dict, stop_names: list) -> str:
    """基于图规划结果生成 Mermaid 路线图（含交通方式标记）"""
    if not path_result or not path_result.get("segments"):
        return ""

    lines = [
        "flowchart LR",
        "    classDef start fill:#10b981,color:#fff,stroke:#059669,stroke-width:2px",
        "    classDef mid fill:#3b82f6,color:#fff,stroke:#2563eb,stroke-width:2px",
        "    classDef end fill:#f59e0b,color:#fff,stroke:#d97706,stroke-width:2px",
        "    classDef ride fill:#f97316,color:#fff,stroke:#ea580c,stroke-width:2px",
        "    classDef transit fill:#8b5cf6,color:#fff,stroke:#7c3aed,stroke-width:2px",
    ]
    lines.append(f'    N0(["{start_name}"]):::start')

    for i, seg in enumerate(path_result["segments"]):
        to_name = seg["to"]
        transport = seg["transport"]
        emoji_t = _TRANSPORT_EMOJI.get(transport, "🚶")
        cls = _TRANSPORT_CLASS.get(transport, "mid")
        mins = round(seg["duration"] / 60)
        label = f"{emoji_t} {mins}分"
        short = _shorten_name(to_name)

        # 判断是不是终点（最后一个 segment 的 to 可能是终点）
        is_last = i == len(path_result["segments"]) - 1
        is_dest = "终点" in to_name or is_last

        if is_dest:
            style, emoji = "end", "🏁"
        else:
            style, emoji = cls, _emoji_for_poi(to_name)

        lines.append(f'    N{i+1}["{emoji} {short}"]:::{style}')

        if transport == "步行":
            lines.append(f'    N{i} -->|"{label}"| N{i+1}')
        elif transport == "骑行":
            lines.append(f'    N{i} ==|"{label}"|==> N{i+1}')
        elif transport == "公交/地铁":
            lines.append(f'    N{i} -.->|"{label}"| N{i+1}')
        else:
            lines.append(f'    N{i} ==|"{label}"|==> N{i+1}')

    return "\n".join(lines)


# ── Leaflet 交互地图 ─────────────────────────────────


def _build_route_html(
    stop_names: list, pois: list, distance_info: str, city: str, user_input: str, start_name: str = "",
    start_coords: tuple = None, dest_name: str = "", dest_coords: tuple = None,
) -> str:
    """生成 Leaflet 交互式地图 HTML（含起终点）"""
    # 构建 POI 坐标映射
    poi_map = {}
    for p in pois:
        lng, lat = p.get("lng"), p.get("lat")
        if lng is not None and lat is not None:
            poi_map[p["name"]] = {
                "lat": lat,
                "lng": lng,
                "rating": p.get("rating"),
                "price": p.get("price_per_person"),
                "address": p.get("address", ""),
                "category": p.get("category", ""),
            }
    # 模糊匹配兜底
    def _lookup(name):
        if name in poi_map:
            return poi_map[name]
        for n, d in poi_map.items():
            if name in n or n in name:
                return d
        return None

    # 起点名称
    if not start_name:
        start_name = "起点"

    # 构建 stops 数据：起点 → POIs → 终点
    stops = []
    if start_coords:
        stops.append({"name": start_name, "lat": start_coords[0], "lng": start_coords[1],
                       "rating": None, "price": None, "address": "", "num": 0})
    else:
        stops.append({"name": start_name, "lat": None, "lng": None,
                       "rating": None, "price": None, "address": "", "num": 0})

    for name in stop_names:
        d = _lookup(name)
        if d:
            stops.append({**d, "name": name, "num": len(stops)})
        else:
            stops.append({"name": name, "lat": None, "lng": None, "rating": None, "price": None,
                          "address": "", "num": len(stops)})

    if dest_name and dest_coords:
        stops.append({"name": dest_name, "lat": dest_coords[0], "lng": dest_coords[1],
                       "rating": None, "price": None, "address": "", "num": len(stops)})
    elif dest_name:
        d = _lookup(dest_name)
        if d:
            stops.append({**d, "name": dest_name, "num": len(stops)})
        else:
            stops.append({"name": dest_name, "lat": None, "lng": None, "rating": None, "price": None,
                          "address": "", "num": len(stops)})

    # 坐标范围（用于地图居中）
    coords = [s for s in stops if s["lat"] is not None]
    if not coords:
        return ""

    lats = [s["lat"] for s in coords]
    lngs = [s["lng"] for s in coords]
    center_lat = (max(lats) + min(lats)) / 2
    center_lng = (max(lngs) + min(lngs)) / 2

    stops_json = json.dumps(stops, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>路线图 - {city}</title>
<style>
/* Leaflet CSS 内联 */
{_static("leaflet.css")}
</style>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  #map {{ height:100vh; width:100vw; }}
  .custom-marker {{ width:32px; height:32px; border-radius:50%; display:flex; align-items:center;
    justify-content:center; color:#fff; font-size:13px; font-weight:700; border:3px solid;
    box-shadow:0 2px 6px rgba(0,0,0,.3); }}
  .marker-start {{ background:#10b981; border-color:#059669; }}
  .marker-mid {{ background:#3b82f6; border-color:#2563eb; }}
  .marker-end {{ background:#f59e0b; border-color:#d97706; }}
  .popup-name {{ font-size:15px; font-weight:700; margin-bottom:4px; }}
  .popup-row {{ font-size:13px; color:#555; line-height:1.6; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
/* Leaflet JS 内联 */
{_static("leaflet.js")}
</script>
<script>
const stops = {stops_json};
const map = L.map('map').setView([{center_lat}, {center_lng}], 14);
L.tileLayer('https://webrd{{s}}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={{x}}&y={{y}}&z={{z}}', {{
  subdomains: ['01','02','03','04'],
  attribution: '© 高德地图 AutoNavi',
  maxZoom: 18,
}}).addTo(map);

// 路线连线
const pts = stops.filter(s => s.lat !== null).map(s => [s.lat, s.lng]);
if (pts.length > 1) {{
  L.polyline(pts, {{ color:'#ef4444', weight:3, opacity:.7, dashArray:'8,5' }}).addTo(map);
  map.fitBounds(pts, {{ padding:[40,40] }});
}}

// 标记点
stops.forEach((s, i) => {{
  if (s.lat === null) return;
  const cls = i === 0 ? 'marker-start' : (i === stops.length-1 ? 'marker-end' : 'marker-mid');
  const icon = L.divIcon({{
    html: `<div class="custom-marker ${{cls}}">${{i+1}}</div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    className: '',
  }});
  const popup = '<div class="popup-name">' + s.name + '</div>'
    + (s.rating ? '<div class="popup-row">⭐ 评分: ' + s.rating + '</div>' : '')
    + (s.price ? '<div class="popup-row">💰 人均: ¥' + s.price + '</div>' : '')
    + (s.address ? '<div class="popup-row">📍 ' + s.address + '</div>' : '');
  L.marker([s.lat, s.lng], {{icon}}).addTo(map).bindPopup(popup);
}});
</script>
</body>
</html>"""
    return html


def _infer_city_from_geocode(place_name: str) -> str:
    """对地名做 geocode，从返回结果中提取城市名（用于无城市时从起点推断）"""
    if not place_name:
        return ""
    try:
        gc = json.loads(geocode(place_name, ""))
        return gc.get("city", "").rstrip("市")
    except Exception:
        return ""


import warnings

def run_agent(user_input: str, session: AgentSession = None) -> tuple:
    """[DEPRECATED] 返回 (回复文本, AgentSession) —— 图路径规划版
    请改用 agent.multi_agent.orchestrator.run_multi_agent()"""
    warnings.warn("run_agent is deprecated, use run_multi_agent instead", DeprecationWarning, stacklevel=2)
    is_new = session is None or not session.city
    if is_new:
        if session is None:
            session = AgentSession()
        city = _extract_city(user_input, session.default_city)
        intent_pre = None  # 复用：当 city 从起点推断时避免重复 LLM 调用
        if not city:
            # 无法从文本提取城市 → 尝试从起点 geocode 反向推断
            intent_pre = _parse_intent(user_input, "")
            origin_pre = intent_pre.get("origin", "")
            city = _infer_city_from_geocode(origin_pre)
        if not city:
            return "请问您在哪个城市？我需要先知道城市才能为你查找地点和规划路线。", session
        session.city = city
        _progress("📍", f"城市：{city}")
        _progress("🎯", f"分析需求：{user_input}")

        # ── 1. LLM 解析意图 ──────────────────────────────
        _progress("🧠", "解析出行意图")
        intent = intent_pre or _parse_intent(user_input, city)
        origin_name = intent.get("origin", "")
        dest_name = intent.get("destination", "")
        keywords = intent.get("keywords", "美食,景点")
        num_stops = min(int(intent.get("num_stops", 3)), 5)
        _progress("   →", f"起点：{origin_name or '未指定'}")
        _progress("   →", f"终点：{dest_name or '由路线决定'}")
        _progress("   →", f"搜索关键词：{keywords}")

        # ── 2. 地理编码（多层兜底） ──────────────────────────
        origin_coords = dest_coords = None

        # LLM 解析失败时，从用户原始输入回退提取地名
        _PLACE_RE = re.compile(
            r"[一-鿿]{2,6}(?:地铁站|轻轨站|高铁站|火车站|汽车站|公交站|"
            r"路|街|巷|道|里|胡同|园|公园|广场|大厦|商场|购物中心|门|楼|塔|"
            r"景区|博物馆|图书馆|医院|学校|大学|学院|机场|码头)"
        )

        if origin_name:
            lat, lng = robust_geocode(origin_name, city)
            if lat is not None:
                origin_coords = (lat, lng)
                _progress("📍", f"起点坐标：{lng},{lat}")
            else:
                # 回退：从原始输入中提取地名重新尝试
                _progress("⚠️", f"起点「{origin_name}」未能解析，尝试从原文提取...")
                matches = _PLACE_RE.findall(user_input)
                for m in matches:
                    lat, lng = robust_geocode(m, city)
                    if lat is not None:
                        origin_name = m
                        origin_coords = (lat, lng)
                        _progress("📍", f"起点坐标(回退)：{lng},{lat} → {m}")
                        break
                if origin_coords is None:
                    _progress("⚠️", f"起点仍未解析，跳过图规划")
        if dest_name:
            lat, lng = robust_geocode(dest_name, city)
            if lat is not None:
                dest_coords = (lat, lng)
                _progress("📍", f"终点坐标：{lng},{lat}")
            else:
                _progress("⚠️", f"终点「{dest_name}」未能解析，尝试从原文提取...")
                matches = _PLACE_RE.findall(user_input)
                for m in matches:
                    if m == origin_name:
                        continue  # 跳过已匹配为起点的
                    lat, lng = robust_geocode(m, city)
                    if lat is not None:
                        dest_name = m
                        dest_coords = (lat, lng)
                        _progress("📍", f"终点坐标(回退)：{lng},{lat} → {m}")
                        break
                if dest_coords is None:
                    _progress("⚠️", f"终点仍未解析")

        # ── 3. 沿途搜索 POI ──────────────────────────────
        _progress("🔍", "在路线沿途搜索 POI")
        raw = _search_corridor_pois(origin_name, dest_name, keywords, city,
                                    origin_coords, dest_coords)
        seen = set()
        all_pois = []
        for p in raw:
            n = p.get("name", "")
            if n and n not in seen:
                seen.add(n)
                all_pois.append(p)

        if not all_pois:
            _progress("⚠️", "沿途未搜到结果，全城兜底")
            for kw in ["美食", "景点"]:
                r = json.loads(search_poi(keywords=kw, location=city, limit=10))
                if isinstance(r, list):
                    all_pois.extend(r)

        # 过滤掉没有坐标的 POI（图规划需要坐标）
        valid_pois = [p for p in all_pois if p.get("lat") is not None and p.get("lng") is not None]
        # 过滤距起点过近（<100m）或同名的 POI（避免起点自己入选）
        if origin_coords:
            filtered = []
            for p in valid_pois:
                d = _haversine(origin_coords[0], origin_coords[1], p["lat"], p["lng"])
                if d >= 100 and origin_name not in p.get("name", ""):
                    filtered.append(p)
            valid_pois = filtered
        # 过滤距终点过近（<100m）或同名的 POI（避免终点重复入选）
        if dest_coords and dest_name:
            filtered = []
            for p in valid_pois:
                d = _haversine(dest_coords[0], dest_coords[1], p["lat"], p["lng"])
                if d >= 100 and dest_name not in p.get("name", ""):
                    filtered.append(p)
            valid_pois = filtered
        _progress("✅", f"获取 {len(all_pois)} 个 POI，其中 {len(valid_pois)} 个含坐标可建图")
        session.all_pois = all_pois

        # ── 4. 建图 + 路径规划 ──────────────────────────
        _progress("🗺️", "构建路线图计算最优路径")
        session.start_name = origin_name or "起点"
        session.dest_name = dest_name or ""

        if origin_coords and valid_pois:
            nodes, graph = build_graph(origin_coords, valid_pois, dest_coords)
            path_result = shortest_path(graph, nodes, num_stops)
        elif dest_coords and valid_pois:
            # 无起点有终点：以终点为锚，环线探索
            _progress("   ↻", "以终点为中心探索周边")
            session.start_name = dest_name + "周边"
            origin_coords = dest_coords  # 后面 HTML map 用
            nodes, graph = build_graph(dest_coords, valid_pois, dest_coords)
            path_result = shortest_path(graph, nodes, num_stops)
        else:
            _progress("⚠️", "坐标不足，跳过图规划")
            path_result = None
            nodes = []

        if path_result is not None:
            session.nodes = nodes
            session.path_result = path_result
            session.stop_names = [
                nodes[nid]["name"] for nid in path_result["node_ids"]
                if nodes[nid]["type"] == "poi"
            ]
            _progress("✅", f"选定 {len(session.stop_names)} 站")
            for seg in path_result["segments"]:
                d = seg["distance"]
                d_str = f"{d}m" if d < 1000 else f"{d/1000:.1f}km"
                _progress("   →", f"{seg['from']} → {seg['to']}（{seg['transport']} {d_str} 约{round(seg['duration']/60)}分钟）")

        _progress("📝", "生成路线方案...")

    # ── 5. 构造最终 prompt ──────────────────────────────
    city = session.city
    if is_new and session.path_result:
        pr = session.path_result
        path_lines = []
        for i, s in enumerate(pr["segments"]):
            mins = round(s["duration"] / 60)
            d = s["distance"]
            path_lines.append(f"{i+1}. {s['from']} → {s['to']}")
            path_lines.append(f"   {s['transport']} {d}米（约{mins}分钟）")
        path_desc = "\n".join(path_lines)

        prompt = (
            f"【起点】{session.start_name}\n"
            f"【终点】{session.dest_name or '由路线自然结束'}\n"
            f"【城市】{city}\n"
            f"【用户需求】{user_input}\n\n"
            f"【算法最优路径】\n{path_desc}\n"
            f"总耗时：{pr['total_duration_min']}分钟\n"
            f"总距离：{pr['total_distance']}米\n\n"
            "请用通俗语言解释这条路线：\n"
            "- 每段为什么选择该交通工具\n"
            "- 各站点推荐什么特色菜/看点\n"
            "- 沿途经过什么地方\n"
            "- 实用建议（最佳时间、注意事项等）\n\n"
            "忠实于算法结果，不修改站点顺序或交通工具。"
        )
    elif is_new:
        prompt = (
            f"【城市】{city}\n【用户需求】{user_input}\n\n"
            "请基于以上需求规划路线。"
        )
    else:
        prompt = (
            f"【上下文】我们在 {city} 规划路线。\n"
            f"【修改要求】{user_input}\n\n"
            "基于已有路线调整。"
        )

    # ── 6. LLM 生成 ─────────────────────────────────────
    messages = session.messages + [{"role": "user", "content": prompt}]
    try:
        data = _call_api(messages, system=SYSTEM_PROMPT)
        response = data["content"][0]["text"]
    except Exception as e:
        response = f"抱歉，AI 服务暂时不可用（{e}），请稍后重试。你的路线已经计算完成，核心数据：起点 {session.start_name}，终点 {session.dest_name or '由路线决定'}，共 {len(session.stop_names)} 个站点。"
        # 仍然输出 Mermaid 图（如果有的话）
        if session.path_result and session.stop_names:
            mermaid = _build_mermaid_from_path(
                session.start_name, session.path_result, session.stop_names
            )
            if mermaid:
                response += f"\n\n---\n\n```mermaid\n{mermaid}\n```"
        _progress("❌", f"LLM 生成失败: {e}")
        return response, session

    # ── 7. Mermaid + HTML 文件输出 ──────────────────────
    if is_new:
        if session.path_result and session.stop_names:
            mermaid = _build_mermaid_from_path(
                session.start_name, session.path_result, session.stop_names
            )
        elif session.stop_names and session.distance_info:
            mermaid = _build_mermaid(
                session.stop_names, session.distance_info, user_input, session.start_name
            )
        else:
            mermaid = ""

        if mermaid:
            response += f"\n\n---\n\n```mermaid\n{mermaid}\n```"
            md_path = Path(__file__).parent.parent / "route_output.md"
            md_path.write_text(f"```mermaid\n{mermaid}\n```", encoding="utf-8")
            _progress("🗺️", f"路线图已保存到 {md_path}")

        # HTML 地图
        html = _build_route_html(
            session.stop_names, session.all_pois, session.distance_info, city,
            user_input, session.start_name,
            start_coords=origin_coords,
            dest_name=session.dest_name,
            dest_coords=dest_coords,
        )
        if html:
            html_path = Path(__file__).parent.parent / "route_output.html"
            html_path.write_text(html, encoding="utf-8")
            _progress("🗺️", f"交互地图已保存到 {html_path}")

    _progress("✅", "路线规划完成")

    session.messages = messages + [{"role": "assistant", "content": response}]
    MAX_HISTORY = 6
    if len(session.messages) > MAX_HISTORY:
        session.messages = session.messages[-MAX_HISTORY:]
    return response, session
