"""路线引擎管线 — 地理编码 + 建图 + 最短路径 + 时间预算约束."""
import re

from app.providers.amap_provider import robust_geocode
from app.algorithms.graph_planner import build_graph, shortest_path, pre_prune_pois
from app.shared.utils import _progress

# 地名正则
_PLACE_RE = re.compile(
    r"[一-鿿]{2,6}(?:地铁站|轻轨站|高铁站|火车站|汽车站|公交站|"
    r"路|街|巷|道|里|胡同|园|公园|广场|大厦|商场|购物中心|门|楼|塔|"
    r"景区|博物馆|图书馆|医院|学校|大学|学院|机场|码头)"
)


def geocode_place(name: str, city: str, user_input: str = "",
                  skip_names: list = None) -> tuple:
    """地理编码单个地名，失败时尝试从 user_input 中正则提取."""
    if not name:
        return None, ""
    skip_names = skip_names or []
    lat, lng = robust_geocode(name, city)
    if lat is not None:
        return (lat, lng), name
    for m in _PLACE_RE.findall(user_input):
        if m in skip_names:
            continue
        lat, lng = robust_geocode(m, city)
        if lat is not None:
            return (lat, lng), m
    return None, name


def run_route_engine(origin_coords, valid_pois: list, dest_coords,
                     num_stops: int, time_budget_hours: float = None) -> dict:
    """建图 → 最短路径，可选时间预算约束."""
    if not origin_coords and not dest_coords:
        return None
    if not valid_pois:
        return None

    anchor = origin_coords or dest_coords
    pruned = pre_prune_pois(valid_pois, max_pois=15,
                            anchor_lat=anchor[0], anchor_lng=anchor[1])
    if len(pruned) < len(valid_pois):
        _progress("   →", f"预剪枝: {len(valid_pois)} → {len(pruned)} POI（保留评分最高）")

    if origin_coords:
        nodes, graph = build_graph(origin_coords, pruned, dest_coords)
        path = shortest_path(graph, nodes, num_stops)
    elif dest_coords:
        nodes, graph = build_graph(dest_coords, pruned, dest_coords)
        path = shortest_path(graph, nodes, num_stops)
    else:
        return None

    if path and time_budget_hours:
        budget_minutes = time_budget_hours * 60
        if path["total_duration_min"] > budget_minutes * 1.2:
            _progress("⏱️", f"总耗时 {path['total_duration_min']} 分超出预算 {budget_minutes} 分，自动减站")
            for reduced in range(num_stops - 1, 0, -1):
                nodes2, graph2 = build_graph(origin_coords or dest_coords, pruned, dest_coords)
                path2 = shortest_path(graph2, nodes2, reduced)
                if path2 and path2["total_duration_min"] <= budget_minutes * 1.1:
                    _progress("   →", f"缩减为 {reduced} 站，耗时 {path2['total_duration_min']} 分")
                    return path2
            _progress("   →", "即使缩减仍超出预算，使用当前最佳方案")
    return path
