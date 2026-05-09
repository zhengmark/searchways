"""罗斯方案 — LLM 工具调用函数与工具定义.

三个工具:
  geocode          — 地名 → 坐标
  query_clusters   — 查询走廊内聚簇摘要
  build_route      — 用选定聚簇 ID 构建实际路线
"""

import json
import math
import time
from functools import lru_cache

from app.providers.amap_provider import robust_geocode, AmapAPIError
from app.algorithms.graph_planner import build_graph, pre_prune_pois, shortest_path
from db.cluster import query_corridor_clusters
from db.connection import get_conn
from db.repository import _row_to_dict

# 工具调用去重缓存（避免 LLM 对相同参数重复调用 build_route）
_DEDUP_CACHE = {}  # key: (tool_name, frozenset_params) → (timestamp, result_json)
_DEDUP_TTL = 30  # 秒

# 关键词 → 品类映射（用于 query_clusters 相关性评分）
_KEYWORD_CATEGORY_MAP = {
    # 美食类
    "美食": ["餐饮", "火锅", "烧烤", "小吃", "甜品", "咖啡", "茶馆", "西餐", "日料", "海鲜", "面馆"],
    "火锅": ["火锅", "餐饮"],
    "烧烤": ["烧烤", "餐饮"],
    "咖啡": ["咖啡", "茶馆"],
    "甜品": ["甜品", "咖啡"],
    "小吃": ["小吃", "餐饮"],
    "清淡": ["茶馆", "咖啡", "甜品", "小吃"],
    "养生": ["茶馆", "咖啡", "小吃"],
    "宵夜": ["火锅", "烧烤", "小吃", "餐饮"],
    "面食": ["面馆", "小吃", "餐饮"],
    "面馆": ["面馆", "小吃", "餐饮"],
    "酒吧": ["酒吧", "餐饮"],
    "夜宵": ["火锅", "烧烤", "小吃", "餐饮", "酒吧"],
    "深夜": ["火锅", "烧烤", "小吃", "酒吧"],
    # 细分品类
    "轻食": ["餐饮", "咖啡", "甜品"],
    "沙拉": ["餐饮", "咖啡"],
    "健康餐": ["餐饮", "咖啡"],
    "素食": ["餐饮", "小吃", "面馆"],
    "有机": ["餐饮", "咖啡"],
    "高蛋白": ["餐饮"],
    "低碳水": ["餐饮", "咖啡"],
    "无糖": ["咖啡", "茶馆", "甜品"],
    "芝士": ["甜品", "咖啡", "西餐"],
    "无辣": ["茶馆", "咖啡", "小吃", "面馆", "西餐", "日料"],
    "包间": ["餐饮", "火锅", "西餐", "日料", "海鲜"],
    # 景点类
    "景点": ["景点", "公园", "博物馆", "古迹", "文化", "剧院"],
    "公园": ["公园", "景点"],
    "博物馆": ["博物馆", "文化"],
    "文化": ["博物馆", "文化", "古迹", "景点"],
    "拍照": ["景点", "公园", "博物馆", "文化"],
    "网红": ["咖啡", "甜品", "景点", "购物"],
    "免费": ["公园", "景点", "博物馆"],
    # 购物类
    "购物": ["购物", "商场"],
    "商场": ["购物", "商场"],
    # 休闲类
    "亲子": ["公园", "景点", "博物馆", "购物", "游乐"],
    "户外": ["公园", "景点"],
    "骑行": ["公园", "景点"],
    "书店": ["文化", "图书馆", "咖啡"],
    "图书馆": ["图书馆", "文化", "咖啡"],
    "安静": ["咖啡", "茶馆", "图书馆", "文化"],
    "文艺": ["咖啡", "茶馆", "文化", "博物馆"],
    "约会": ["咖啡", "甜品", "西餐", "日料", "景点", "公园", "茶馆", "小吃"],
    "女朋友": ["咖啡", "甜品", "西餐", "景点", "公园", "拍照", "小吃"],
    "情侣": ["咖啡", "甜品", "西餐", "景点", "公园", "拍照", "剧院"],
    "带孩子": ["公园", "景点", "博物馆", "购物", "游乐"],
    "带老人": ["公园", "景点", "博物馆", "茶馆"],
    "室内": ["博物馆", "购物", "咖啡", "图书馆", "剧院"],
    "雨天": ["博物馆", "购物", "咖啡", "图书馆", "剧院"],
    # 运动/健身
    "健身房": ["运动健身"],
    "运动": ["运动健身", "公园"],
    "健身": ["运动健身", "餐饮"],
    "按摩": ["休闲", "运动健身"],
    "SPA": ["休闲"],
    # 茶/棋牌
    "茶": ["茶馆", "咖啡"],
    "喝茶": ["茶馆"],
    "下棋": ["茶馆", "休闲"],
    "棋牌": ["休闲", "茶馆"],
    "茶馆": ["茶馆", "咖啡"],
    # 商务/高档
    "商务": ["餐饮", "西餐", "火锅", "日料", "海鲜"],
    "高档": ["餐饮", "西餐", "日料", "海鲜", "火锅"],
    "宴请": ["餐饮", "西餐", "海鲜", "火锅"],
    "客户": ["餐饮", "西餐", "日料", "海鲜"],
    # 穷游/预算
    "穷游": ["公园", "景点", "博物馆", "小吃"],
    "便宜": ["小吃", "面馆", "公园", "景点"],
}

# POI 名称黑名单关键词 — 包含这些词的簇相关性降权
_POI_NAME_BLACKLIST = [
    "KTV", "ktv", "Ktv", "纯K", "K ",
    "棋牌", "麻将",
    "商务会所", "洗浴", "足浴",
    "手机", "专卖店", "小米之家",
    "舞厅", "歌厅",
    "中介", "房产",
    "Party.K", "party.k",
]


def _cluster_has_blacklist_name(cluster: dict) -> bool:
    """检查簇的 top_poi_names 是否含黑名单词."""
    names = cluster.get("top_poi_names", [])
    for name in names:
        for bl in _POI_NAME_BLACKLIST:
            if bl.lower() in name.lower():
                return True
    return False


def _cluster_relevance(cluster: dict, keywords: list) -> float:
    """计算聚簇与关键词的相关性 0.0-1.0."""
    if not keywords:
        return 0.5  # 无偏好时中等相关
    top_cats = [c.lower() for c in cluster.get("top_cats", [])]
    score = 0.0
    for kw in keywords:
        kw_lower = kw.lower()
        match_cats = _KEYWORD_CATEGORY_MAP.get(kw_lower, [kw_lower])
        for mc in match_cats:
            if any(mc.lower() in tc for tc in top_cats):
                score += 1.0 / len(keywords)
                break
        else:
            # 模糊匹配：关键词是否出现在 top_cats 名字中
            if any(kw_lower in tc for tc in top_cats):
                score += 0.5 / len(keywords)

    # 黑名单降权：含 KTV/棋牌/手机店等词 → 得分减半
    if _cluster_has_blacklist_name(cluster):
        score *= 0.5

    return min(score, 1.0)


# ── 工具定义（Anthropic 格式）───────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "geocode",
        "description": "将地名解析为经纬度坐标。调用一次解析一个地点。",
        "input_schema": {
            "type": "object",
            "properties": {
                "place": {
                    "type": "string",
                    "description": "需要解析的地名，如'丈八六路地铁站'、'钟楼'",
                },
                "city": {
                    "type": "string",
                    "description": "所在城市，如'西安'、'北京'",
                },
            },
            "required": ["place", "city"],
        },
    },
    {
        "name": "query_clusters",
        "description": (
            "查询起终点走廊内的 POI 聚簇，每个簇包含品类、价格、评分摘要和 projection（0=起点, 1=终点，表示簇在路线上的位置）。"
            "结果已按 projection 排序，确保全程均匀分布。用这个工具了解沿途有哪些可选的商圈/美食聚集区，再从中挑选合适的聚簇来构建路线。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin_lat": {
                    "type": "number",
                    "description": "起点纬度",
                },
                "origin_lng": {
                    "type": "number",
                    "description": "起点经度",
                },
                "dest_lat": {
                    "type": "number",
                    "description": "终点纬度（无终点时可不传）",
                },
                "dest_lng": {
                    "type": "number",
                    "description": "终点经度（无终点时可不传）",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "感兴趣的品类关键词，如 ['美食', '咖啡', '景点']",
                },
                "budget": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "预算水平：low(人均<40), medium(30-100), high(>80)",
                },
            },
            "required": ["origin_lat", "origin_lng"],
        },
    },
    {
        "name": "build_route",
        "description": (
            "用选定的聚簇 ID 构建实际路线。传入挑选的簇 ID 列表和期望的停靠站数，"
            "系统会自动从每个簇中选取评分最高的 POI，计算最优路径并返回路线详情。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "选中的聚簇 ID 列表，按访问顺序排列",
                },
                "num_stops": {
                    "type": "integer",
                    "description": "期望的停靠站数，应 ≤ 选中的 cluster_ids 数量。每个簇最多产出一个站，以保证多样性",
                },
            },
            "required": ["cluster_ids", "num_stops"],
        },
    },
]


# ── 工具实现 ───────────────────────────────────────────

def tool_geocode(place: str, city: str) -> dict:
    """geocode 工具实现."""
    try:
        lat, lng = robust_geocode(place, city)
        if lat is not None and lng is not None:
            return {
                "success": True,
                "place": place,
                "lat": lat,
                "lng": lng,
            }
        return {"success": False, "error": f"未找到'{place}'的坐标，请尝试更具体的地名"}
    except AmapAPIError:
        return {"success": False, "error": f"地理编码服务暂不可用，请稍后重试'{place}'"}


def tool_query_clusters(origin_lat: float, origin_lng: float,
                        dest_lat: float = None, dest_lng: float = None,
                        keywords: list = None, budget: str = None,
                        city: str = None) -> dict:
    """query_clusters 工具实现."""
    clusters = query_corridor_clusters(
        origin_lat, origin_lng,
        dest_lat=dest_lat, dest_lng=dest_lng,
        keywords=keywords, budget=budget,
    )
    # 计算相关性并排序
    kws = keywords or []
    for c in clusters:
        c["_relevance"] = _cluster_relevance(c, kws)
    clusters.sort(key=lambda c: (c["_relevance"], c.get("avg_rating", 0) or 0), reverse=True)

    # 精简返回给 LLM 的字段
    summary = []
    for c in clusters:
        summary.append({
            "cluster_id": c["cluster_id"],
            "name": c["name"],
            "dist_from_origin_km": c["dist_from_origin_km"],
            "projection": c.get("projection", 0),
            "poi_count": c["poi_count"],
            "top_cats": c["top_cats"],
            "avg_rating": c["avg_rating"],
            "avg_price": c["avg_price"],
            "top_poi_names": c["top_poi_names"][:3],
            "keyword_match": round(c["_relevance"], 1),  # 0=不匹配, 0.5=中性, 1=高度匹配
        })
    # 筛掉完全无关的簇（match=0），保留至少 5 个
    relevant = [s for s in summary if s["keyword_match"] > 0]
    if len(relevant) >= 3:
        summary = relevant

    # 高德 API 补搜：本地 DB 结果不足或匹配度低时 fallback

    # 特殊品类 —— DB 覆盖弱，必触发补搜
    _SPECIAL_FALLBACK_KW = {"轻食", "沙拉", "健康餐", "素食", "有机", "健身房", "运动健身",
                            "包间", "商务宴请", "按摩", "SPA", "高蛋白", "低碳水", "无糖"}
    has_special = bool(set(kw.lower() for kw in kws) & _SPECIAL_FALLBACK_KW)

    low_match = [s for s in summary if s["keyword_match"] < 0.5]
    if len(summary) < 3 or has_special or len(low_match) >= len(summary) * 0.5:
        amap_pois = _amap_fallback_search(origin_lat, origin_lng, kws, dest_lat, dest_lng, city=city or "西安")
        if amap_pois:
            summary.append({
                "cluster_id": -1,
                "name": "高德补搜结果",
                "dist_from_origin_km": 0,
                "poi_count": len(amap_pois),
                "top_cats": list(set(p.get("category", "") for p in amap_pois[:10])),
                "avg_rating": round(sum(p.get("rating", 0) or 0 for p in amap_pois)/max(len(amap_pois),1), 1),
                "avg_price": round(sum(p.get("price_per_person", 0) or 0 for p in amap_pois)/max(len(amap_pois),1)),
                "top_poi_names": [p.get("name","") for p in amap_pois[:5]],
                "keyword_match": 0.5,
                "source": "amap",
            })

    # ── 热门景点发现：模糊/通用查询时注入城市热门景点 ──
    _GENERIC_KW = {"美食", "景点", "好玩", "逛逛", "推荐", "打卡", "好吃", "转", "玩"}
    _is_generic = len(kws) <= 2 and all(
        any(g in kw for g in _GENERIC_KW) for kw in kws
    )
    if _is_generic or len(summary) < 4:
        city_for_search = city or "西安"
        famous_pois = []
        # 1) 高德热门景点搜索
        try:
            from app.providers.amap_provider import search_top_attractions
            loc = f"{origin_lng},{origin_lat}" if origin_lng and origin_lat else None
            famous_pois = search_top_attractions(city_for_search, location=loc, limit=8)
        except Exception:
            pass
        # 2) 精选列表兜底
        if len(famous_pois) < 3:
            try:
                from app.providers.amap_provider import geocode
                from app.shared.constants import FAMOUS_ATTRACTIONS
                curated = FAMOUS_ATTRACTIONS.get(city_for_search, [])
                for att in curated:
                    try:
                        gc = geocode(att["name"], city_for_search)
                        famous_pois.append({
                            "name": att["name"],
                            "category": att.get("category", "景点"),
                            "lat": gc["lat"], "lng": gc["lng"],
                            "rating": None, "price_per_person": None,
                            "address": "",
                        })
                    except Exception:
                        pass
            except Exception:
                pass
        if famous_pois:
            summary.insert(0, {
                "cluster_id": -2,
                "name": f"{city_for_search}热门景点推荐",
                "dist_from_origin_km": 0,
                "poi_count": len(famous_pois),
                "top_cats": list(set(p.get("category", "景点") for p in famous_pois[:5])),
                "avg_rating": round(sum(p.get("rating", 0) or 0 for p in famous_pois) / max(len(famous_pois), 1), 1),
                "avg_price": 0,
                "top_poi_names": [p.get("name", "") for p in famous_pois[:5]],
                "keyword_match": 0.85,
                "source": "famous",
            })
            # 缓存供 build_route 使用
            cache_key = f"famous_{city_for_search}"
            _AMAP_FALLBACK_CACHE[cache_key] = famous_pois

    return {"success": True, "clusters": summary, "total": len(summary)}


# 高德补搜 POI 缓存（agent_state 间接引用）
_AMAP_FALLBACK_CACHE = {}  # session_id → [poi_dicts]


# 特殊关键词 → 高德 API 友好搜索词
_AMAP_KW_MAP = {
    "轻食": "轻食沙拉", "沙拉": "沙拉", "健康餐": "轻食",
    "素食": "素食餐厅", "有机": "有机餐厅",
    "高蛋白": "轻食沙拉", "低碳水": "轻食", "无糖": "咖啡甜品",
    "健身房": "健身房", "健身": "健身房", "运动": "运动健身",
    "包间": "中餐包间", "商务": "高档中餐", "宴请": "高档餐厅",
    "客户": "商务餐厅", "高档": "高档餐厅",
    "按摩": "按摩", "SPA": "SPA",
    "喝茶": "茶馆", "下棋": "茶馆棋牌", "棋牌": "棋牌",
}


def _amap_fallback_search(origin_lat, origin_lng, keywords, dest_lat=None, dest_lng=None, city="西安"):
    """高德 API 补搜 — 本地 DB 无结果时兜底."""
    try:
        from app.providers.amap_provider import search_poi
        from app.algorithms.poi_filter import dedup_pois
        from app.algorithms.geo import haversine

        all_pois = []
        # 将特殊关键词映射为高德 API 友好搜索词
        search_kws = []
        for kw in (keywords or ["美食"])[:3]:
            mapped = _AMAP_KW_MAP.get(kw)
            if mapped and mapped not in search_kws:
                search_kws.append(mapped)
            elif kw not in search_kws:
                search_kws.append(kw)
        # 最多 3 个不同搜索词
        search_kws = search_kws[:3]

        for kw in search_kws:
            try:
                pois = search_poi(keywords=kw, city=city,
                                  location=f"{origin_lng},{origin_lat}",
                                  radius=5000, offset=10)
                all_pois.extend(pois)
            except Exception:
                continue

        if not all_pois:
            return []

        all_pois = dedup_pois(all_pois)

        # 按评分排序 + 距离筛选
        for p in all_pois:
            lat = p.get("lat")
            lng = p.get("lng")
            if lat is not None and lng is not None:
                p["_dist"] = haversine(origin_lat, origin_lng, lat, lng)

        all_pois.sort(key=lambda p: (p.get("rating", 0) or 0, -(p.get("_dist", 999))), reverse=True)
        result = all_pois[:15]

        # 缓存到模块变量，供 build_route 使用
        cache_id = f"{origin_lat:.4f}_{origin_lng:.4f}"
        _AMAP_FALLBACK_CACHE[cache_id] = result

        return result
    except Exception:
        return []


def _load_amap_pois(origin_coords, city=""):
    """从缓存加载高德补搜和热门景点 POI."""
    pois = []
    # 高德补搜缓存（按坐标）
    if origin_coords:
        cache_id = f"{origin_coords[0]:.4f}_{origin_coords[1]:.4f}"
        pois.extend(_AMAP_FALLBACK_CACHE.get(cache_id, []))
    # 热门景点缓存（按城市）
    if city:
        famous_id = f"famous_{city}"
        famous_pois = _AMAP_FALLBACK_CACHE.get(famous_id, [])
        # 去重
        seen = {p.get("name","") for p in pois}
        for p in famous_pois:
            if p.get("name","") not in seen:
                pois.append(p)
    return pois


def tool_build_route(cluster_ids: list, num_stops: int,
                     origin_coords: tuple, dest_coords: tuple = None,
                     dest_name: str = "",
                     amap_pois: list = None) -> dict:
    """build_route 工具实现.

    Args:
        cluster_ids: 选中的聚簇 ID 列表（含 -1 高德补搜, -2 热门景点）
        num_stops: 停靠站数
        origin_coords: (lat, lng) 起点坐标
        dest_coords: (lat, lng) 终点坐标（可选）
        dest_name: 终点名称
        amap_pois: 高德补搜的 POI 列表（当 cluster_ids 含 -1 或 -2 时使用）
    """
    if not cluster_ids:
        return {"success": False, "error": "请至少选择一个聚簇"}

    # num_stops 不能超过聚簇数（每个簇最多选1个POI以保证多样性）
    num_stops = min(num_stops, len(cluster_ids))

    # 步骤1: 加载 POI — DB 或高德补搜/热门景点
    pois = []
    amap_cluster_requested = -1 in cluster_ids or -2 in cluster_ids

    if amap_pois:
        pois = [dict(p) for p in amap_pois]
        for p in pois:
            p.setdefault("cluster_id", -1)
    elif amap_cluster_requested:
        pass

    # 从 DB 加载（常规 cluster_ids，排除 -1 和 -2）
    db_cluster_ids = [c for c in cluster_ids if c >= 0]
    if db_cluster_ids:
        with get_conn() as conn:
            placeholders = ",".join("?" * len(db_cluster_ids))
            rows = conn.execute(f"""
                SELECT * FROM pois
                WHERE cluster_id IN ({placeholders})
                  AND lat IS NOT NULL
                ORDER BY rating DESC NULLS LAST
                LIMIT 100
            """, db_cluster_ids).fetchall()
        pois.extend([_row_to_dict(r) for r in rows])

    if not pois:
        return {"success": False, "error": "选中的聚簇中没有可用 POI"}

    # 步骤2: 预剪枝（保留 top-15 评分 POI）
    anchor_lat = origin_coords[0]
    anchor_lng = origin_coords[1]
    pois = pre_prune_pois(pois, max_pois=15, anchor_lat=anchor_lat, anchor_lng=anchor_lng)

    # 步骤3: 建图
    nodes, graph = build_graph(origin_coords, pois, dest_coords)

    # 步骤4: 选路径
    actual_stops = min(num_stops, len(pois))
    path_result = shortest_path(graph, nodes, actual_stops)

    if not path_result or not path_result.get("segments"):
        return {"success": False, "error": "无法从选中聚簇构建有效路线"}

    # 步骤5: 构建返回结果
    stop_names = []
    stop_details = []
    for seg in path_result["segments"]:
        to_name = seg["to"]
        if to_name not in ("起点", "终点"):
            # 查找 POI 详情
            poi_info = next((p for p in pois if p["name"] == to_name), None)
            stop_names.append(to_name)
            stop_details.append({
                "name": to_name,
                "lat": poi_info.get("lat") if poi_info else None,
                "lng": poi_info.get("lng") if poi_info else None,
                "category": poi_info.get("category", "") if poi_info else "",
                "rating": poi_info.get("rating") if poi_info else None,
                "price_per_person": poi_info.get("price_per_person") if poi_info else None,
                "address": poi_info.get("address", "") if poi_info else "",
                "transport_from_prev": seg["transport"],
                "duration_min": round(seg["duration"] / 60),
                "distance_m": seg["distance"],
            })

    return {
        "success": True,
        "stops": stop_details,
        "total_duration_min": path_result["total_duration_min"],
        "total_distance_m": path_result["total_distance"],
        "num_stops": len(stop_details),
    }


# ── 工具调度 ───────────────────────────────────────────

def execute_tool(tool_name: str, tool_input: dict, agent_state: dict) -> str:
    """执行工具调用并返回 JSON 字符串结果.

    Args:
        tool_name: 工具名（geocode / query_clusters / build_route）
        tool_input: LLM 传来的参数
        agent_state: 当前 agent 状态（包含 origin_coords, dest_coords, city 等）

    Returns:
        JSON 字符串，可直接用于 tool_result 消息
    """
    # 去重缓存检查（避免 LLM 对相同参数重复调用）
    if tool_name == "build_route":
        cache_key = (
            "build_route",
            json.dumps(sorted(tool_input.get("cluster_ids", [])), sort_keys=True),
            tool_input.get("num_stops"),
        )
    elif tool_name == "query_clusters":
        cache_key = (
            "query_clusters",
            tool_input.get("origin_lat"),
            tool_input.get("origin_lng"),
            tool_input.get("dest_lat"),
            tool_input.get("dest_lng"),
            json.dumps(sorted(tool_input.get("keywords", [])), sort_keys=True),
            tool_input.get("budget"),
            agent_state.get("city", ""),
        )
    else:
        cache_key = None

    if cache_key is not None:
        cached = _DEDUP_CACHE.get(cache_key)
        if cached:
            ts, result = cached
            if time.time() - ts < _DEDUP_TTL:
                return result
            else:
                del _DEDUP_CACHE[cache_key]

    if tool_name == "geocode":
        result = tool_geocode(
            place=tool_input.get("place", ""),
            city=tool_input.get("city", ""),
        )
        if result.get("success"):
            # 第一个 geocode 调用 → origin，第二个 → destination
            if agent_state.get("origin_coords") is None:
                agent_state["origin_coords"] = (result["lat"], result["lng"])
                agent_state["start_name"] = result["place"]
            else:
                agent_state["dest_coords"] = (result["lat"], result["lng"])
                agent_state["dest_name"] = result["place"]
            # 从 geocode 参数中提取城市
            city = tool_input.get("city", "").rstrip("市")
            if city and not agent_state.get("city"):
                agent_state["city"] = city
    elif tool_name == "query_clusters":
        default_origin = agent_state.get("origin_coords", (0, 0))
        default_dest = agent_state.get("dest_coords")
        # 保存本次查询的关键词和预算到 agent_state，供多轮复用
        kws = tool_input.get("keywords")
        if kws:
            agent_state["last_keywords"] = kws
        budget = tool_input.get("budget")
        if budget:
            agent_state["last_budget"] = budget
        result = tool_query_clusters(
            origin_lat=tool_input.get("origin_lat", default_origin[0]),
            origin_lng=tool_input.get("origin_lng", default_origin[1]),
            dest_lat=tool_input.get("dest_lat", default_dest[0] if default_dest else None),
            dest_lng=tool_input.get("dest_lng", default_dest[1] if default_dest else None),
            keywords=kws,
            budget=budget,
            city=agent_state.get("city"),
        )
        # 保存所有查询到的簇 ID（供走廊引擎用，不管 LLM 选没选）
        if result.get("success") and result.get("clusters"):
            all_ids = [c["cluster_id"] for c in result["clusters"] if c["cluster_id"] > 0]
            if all_ids:
                agent_state["all_corridor_cluster_ids"] = all_ids
            for c in result["clusters"]:
                if c.get("source") == "amap":
                    agent_state["has_amap_fallback"] = True
                    break
    elif tool_name == "build_route":
        origin_coords = agent_state.get("origin_coords")
        if not origin_coords:
            return json.dumps({"success": False, "error": "缺少起点坐标，请先调用 geocode"},
                              ensure_ascii=False)
        # 加载高德补搜 POI（如果有）
        amap_pois = []
        if -1 in tool_input.get("cluster_ids", []) or -2 in tool_input.get("cluster_ids", []):
            amap_pois = _load_amap_pois(origin_coords, city=agent_state.get("city", ""))
        result = tool_build_route(
            cluster_ids=tool_input.get("cluster_ids", []),
            num_stops=tool_input.get("num_stops", 3),
            origin_coords=origin_coords,
            dest_coords=agent_state.get("dest_coords"),
            dest_name=agent_state.get("dest_name", ""),
            amap_pois=amap_pois if amap_pois else None,
        )
        if result.get("success"):
            agent_state["stop_names"] = [s["name"] for s in result["stops"]]
            agent_state["path_result"] = result
            # 将 POI 详情存入 agent_state（含坐标，供 web 地图用）
            agent_state["all_pois"] = [
                {
                    "name": s["name"],
                    "lat": s.get("lat"),
                    "lng": s.get("lng"),
                    "category": s.get("category", ""),
                    "rating": s.get("rating"),
                    "price_per_person": s.get("price_per_person"),
                    "address": s.get("address", ""),
                }
                for s in result["stops"]
            ]

            # ── 构建走廊数据（供前端交互式编辑用）──
            # 用全部 corridor 簇（不只是 LLM 选的），确保推荐 POI 覆盖全路线
            all_cluster_ids = agent_state.get("all_corridor_cluster_ids",
                                tool_input.get("cluster_ids", []))
            keywords = agent_state.get("last_keywords", [])
            budget = agent_state.get("last_budget", "")
            dest_coords = agent_state.get("dest_coords")
            try:
                from app.pipeline.corridor_engine import build_corridor
                corridor_data = build_corridor(
                    origin_coords, dest_coords, all_cluster_ids,
                    keywords=keywords, budget=budget,
                )
                agent_state["corridor_data"] = corridor_data
            except Exception:
                agent_state["corridor_data"] = {
                    "corridor_pois": [], "cluster_markers": [], "corridor_shape": [],
                }

    result_json = json.dumps(result, ensure_ascii=False, default=str)

    # 成功的 build_route 或 query_clusters 结果写入去重缓存
    if (tool_name == "build_route" and result.get("success")) or \
       (tool_name == "query_clusters" and result.get("success")):
        _DEDUP_CACHE[cache_key] = (time.time(), result_json)

    return result_json
