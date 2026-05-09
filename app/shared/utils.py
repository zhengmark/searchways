"""共享工具函数 —— 城市提取、进度打印、Mermaid/HTML 渲染."""
import json
import re
from pathlib import Path

# ── Leaflet 静态资源 ─────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_STATIC_DIR = _PROJECT_ROOT / "web" / "static" / "assets" / "leaflet"
_STATIC = {}

def _static(name):
    if name not in _STATIC:
        p = _STATIC_DIR / name
        _STATIC[name] = p.read_text(encoding="utf-8") if p.exists() else ""
    return _STATIC[name]


# ── AgentSession ─────────────────────────────────────

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
        # 持久化用 — 跨轮次恢复
        self.origin_coords = None
        self.dest_coords = None
        self.num_stops = 3
        self.keywords = "美食,景点"
        self.last_user_input = ""
        self.review_score = 0
        self.budget = ""
        self.violations = []
        self.last_clusters_hint = []

        # ── Phase 2-4: 交互式编辑字段 ──
        self.corridor_pois: list = []         # 走廊内所有候选 POI
        self.corridor_clusters: list = []     # 簇中心坐标（画椭圆用）
        self.corridor_shape: list = []        # 类椭圆包络多边形 [[lat,lng],...]
        self.selected_poi_ids: list = []      # 用户确认选中的 POI ID
        self.removed_poi_ids: list = []       # 用户主动移除的 POI ID
        self.route_confirmed: bool = False    # 是否已点"确认路线"
        self.graph_data: dict | None = None   # 序列化图（避免重复 ~80 次 API 调用）
        self.recommendation_reasons: dict = {}  # {poi_id: {structured, user_need}}
        self.transit_preferences: dict = {}   # {mode, avoid_transfers, ...}

    def to_dict(self) -> dict:
        """JSON 序列化."""
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, tuple):
                d[k] = list(v)
            elif isinstance(v, set):
                d[k] = list(v)
            else:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AgentSession":
        """从 JSON 反序列化."""
        s = cls()
        for k, v in d.items():
            if k == "origin_coords" and isinstance(v, list) and len(v) == 2:
                v = tuple(v)
            if k == "dest_coords" and isinstance(v, list) and len(v) == 2:
                v = tuple(v)
            if hasattr(s, k):
                setattr(s, k, v)
        return s


# ── 进度打印 ──────────────────────────────────────────

def _progress(emoji: str, msg: str, callback=None):
    if callback:
        callback(emoji, msg)
    else:
        print(f"  {emoji}  {msg}")


# ── 城市提取 ──────────────────────────────────────────

_ALL_CITIES = [
    "北京", "上海", "天津", "重庆",
    "石家庄", "唐山", "秦皇岛", "邯郸", "保定", "张家口", "承德", "沧州", "廊坊", "衡水", "太原", "大同",
    "阳泉", "长治", "晋城", "朔州", "忻州", "吕梁", "晋中", "临汾", "运城", "呼和浩特", "包头", "乌海",
    "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布",
    "沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭",
    "朝阳", "葫芦岛", "长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城", "延边", "哈尔滨",
    "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化",
    "南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州",
    "宿迁", "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水",
    "合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州",
    "六安", "亳州", "池州", "宣城", "福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩",
    "宁德", "南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶",
    "济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂",
    "德州", "聊城", "滨州", "菏泽",
    "郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡",
    "南阳", "商丘", "信阳", "周口", "驻马店", "武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门",
    "孝感", "荆州", "黄冈", "咸宁", "随州", "长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德",
    "张家界", "益阳", "郴州", "永州", "怀化", "娄底",
    "广州", "深圳", "珠海", "汕头", "佛山", "韶关", "湛江", "肇庆", "江门", "茂名", "惠州", "梅州",
    "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮", "南宁", "柳州", "桂林",
    "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左", "海口",
    "三亚", "三沙",
    "成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "南充", "眉山",
    "宜宾", "广安", "达州", "雅安", "巴中", "资阳", "贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁",
    "昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "拉萨", "日喀则",
    "西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛", "兰州", "嘉峪关",
    "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南", "西宁", "银川",
    "乌鲁木齐", "克拉玛依", "吐鲁番", "哈密",
    "香港", "澳门", "台北", "新北", "桃园", "台中", "台南", "高雄",
    "大理", "丽江", "香格里拉", "腾冲", "瑞丽", "景洪", "凯里", "都江堰", "峨眉山", "武夷山", "黄山",
    "庐山", "张家界", "凤凰", "婺源", "平遥", "敦煌", "哈密", "喀什", "伊宁", "满洲里", "延吉",
]
_CITY_PATTERN = re.compile("|".join(re.escape(c) for c in _ALL_CITIES))


def _extract_city(user_input: str, default_city: str = "") -> str:
    match = _CITY_PATTERN.search(user_input)
    if match:
        return match.group(0)
    m = re.search(r"([一-鿿]{2,3})市", user_input)
    if m:
        return m.group(1)
    if default_city:
        return default_city
    return ""


def _infer_city_from_geocode(place_name: str) -> str:
    from app.providers.amap_provider import geocode, AmapAPIError
    if not place_name:
        return ""
    try:
        gc = geocode(place_name, "")
        return gc.get("city", "").rstrip("市")
    except AmapAPIError:
        return ""


# ── 地名正则 ──────────────────────────────────────────

_PLACE_RE = re.compile(
    r"[一-鿿]{2,6}(?:地铁站|轻轨站|高铁站|火车站|汽车站|公交站|"
    r"路|街|巷|道|里|胡同|园|公园|广场|大厦|商场|购物中心|门|楼|塔|"
    r"景区|博物馆|图书馆|医院|学校|大学|学院|机场|码头)"
)


# ── Mermaid / HTML 渲染 ───────────────────────────────

def _shorten_name(name: str) -> str:
    for sep in ["(", "（", "·", "—", "-"]:
        if sep in name:
            name = name.split(sep)[0]
    return name.strip()


def _emoji_for_poi(name: str, category: str = "") -> str:
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


_TRANSPORT_EMOJI = {"步行": "🚶", "骑行": "🚲", "公交/地铁": "🚌", "打车": "🚕"}
_TRANSPORT_CLASS = {"步行": "mid", "骑行": "ride", "公交/地铁": "transit", "打车": "ride"}


def _build_mermaid_from_path(start_name: str, path_result: dict, stop_names: list) -> str:
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


def _build_route_html(
    stop_names: list, pois: list, distance_info: str, city: str, user_input: str, start_name: str = "",
    start_coords: tuple = None, dest_name: str = "", dest_coords: tuple = None,
) -> str:
    poi_map = {}
    for p in pois:
        lng, lat = p.get("lng"), p.get("lat")
        if lng is not None and lat is not None:
            poi_map[p["name"]] = {
                "lat": lat, "lng": lng,
                "rating": p.get("rating"), "price": p.get("price_per_person"),
                "address": p.get("address", ""), "category": p.get("category", ""),
            }

    def _lookup(name):
        if name in poi_map:
            return poi_map[name]
        for n, d in poi_map.items():
            if name in n or n in name:
                return d
        return None

    if not start_name:
        start_name = "起点"

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

const pts = stops.filter(s => s.lat !== null).map(s => [s.lat, s.lng]);
if (pts.length > 1) {{
  L.polyline(pts, {{ color:'#ef4444', weight:3, opacity:.7, dashArray:'8,5' }}).addTo(map);
  map.fitBounds(pts, {{ padding:[40,40] }});
}}

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
