"""罗斯方案 — LLM 工具调用函数与工具定义.

三个工具:
  geocode          — 地名 → 坐标
  query_clusters   — 查询走廊内聚簇摘要
  build_route      — 用选定聚簇 ID 构建实际路线
"""

import json
import math

from app.providers.amap_provider import robust_geocode, AmapAPIError
from app.algorithms.graph_planner import build_graph, pre_prune_pois, shortest_path
from db.cluster import query_corridor_clusters
from db.connection import get_conn
from db.repository import _row_to_dict


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
            "查询起终点走廊内的 POI 聚簇，每个簇包含品类、价格、评分摘要。"
            "用这个工具了解沿途有哪些可选的商圈/美食聚集区，再从中挑选合适的聚簇来构建路线。"
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
                        keywords: list = None, budget: str = None) -> dict:
    """query_clusters 工具实现."""
    clusters = query_corridor_clusters(
        origin_lat, origin_lng,
        dest_lat=dest_lat, dest_lng=dest_lng,
        keywords=keywords, budget=budget,
    )
    # 精简返回给 LLM 的字段
    summary = []
    for c in clusters:
        summary.append({
            "cluster_id": c["cluster_id"],
            "name": c["name"],
            "dist_from_origin_km": c["dist_from_origin_km"],
            "poi_count": c["poi_count"],
            "top_cats": c["top_cats"],
            "avg_rating": c["avg_rating"],
            "avg_price": c["avg_price"],
            "top_poi_names": c["top_poi_names"][:3],
        })
    return {"success": True, "clusters": summary, "total": len(summary)}


def tool_build_route(cluster_ids: list, num_stops: int,
                     origin_coords: tuple, dest_coords: tuple = None,
                     dest_name: str = "") -> dict:
    """build_route 工具实现.

    Args:
        cluster_ids: 选中的聚簇 ID 列表
        num_stops: 停靠站数
        origin_coords: (lat, lng) 起点坐标
        dest_coords: (lat, lng) 终点坐标（可选）
        dest_name: 终点名称
    """
    if not cluster_ids:
        return {"success": False, "error": "请至少选择一个聚簇"}

    # num_stops 不能超过聚簇数（每个簇最多选1个POI以保证多样性）
    num_stops = min(num_stops, len(cluster_ids))

    # 步骤1: 从选中聚簇加载 POI
    with get_conn() as conn:
        placeholders = ",".join("?" * len(cluster_ids))
        rows = conn.execute(f"""
            SELECT * FROM pois
            WHERE cluster_id IN ({placeholders})
              AND lat IS NOT NULL
            ORDER BY rating DESC NULLS LAST
            LIMIT 100
        """, cluster_ids).fetchall()

    if not rows:
        return {"success": False, "error": "选中的聚簇中没有可用 POI"}

    pois = [_row_to_dict(r) for r in rows]

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
        result = tool_query_clusters(
            origin_lat=tool_input.get("origin_lat", default_origin[0]),
            origin_lng=tool_input.get("origin_lng", default_origin[1]),
            dest_lat=tool_input.get("dest_lat", default_dest[0] if default_dest else None),
            dest_lng=tool_input.get("dest_lng", default_dest[1] if default_dest else None),
            keywords=tool_input.get("keywords"),
            budget=tool_input.get("budget"),
        )
    elif tool_name == "build_route":
        origin_coords = agent_state.get("origin_coords")
        if not origin_coords:
            return json.dumps({"success": False, "error": "缺少起点坐标，请先调用 geocode"},
                              ensure_ascii=False)
        result = tool_build_route(
            cluster_ids=tool_input.get("cluster_ids", []),
            num_stops=tool_input.get("num_stops", 3),
            origin_coords=origin_coords,
            dest_coords=agent_state.get("dest_coords"),
            dest_name=agent_state.get("dest_name", ""),
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
    else:
        result = {"success": False, "error": f"未知工具: {tool_name}"}

    return json.dumps(result, ensure_ascii=False, default=str)
