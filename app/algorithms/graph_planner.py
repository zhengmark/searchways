"""图路线规划引擎 — 加权图建模 + 投影分段路径选取."""
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.algorithms.routing import get_route, decide_transport
from app.algorithms.geo import haversine, project_ratio

# 每个节点只对最近的 K 个邻居调用真实路线 API
_API_DISTANCE_THRESHOLD_M = 3000
_K_NEAREST = 8


class SafeGraph:
    """Wrapper around graph adjacency matrix that safely handles None edges."""
    
    def __init__(self, graph: list, nodes: list):
        self._g = graph
        self._n = nodes
        self._n_len = len(graph) if graph else 0
    
    def edge(self, i: int, j: int) -> dict:
        """Get edge (i,j) safely. Returns sentinel values for missing edges."""
        try:
            if 0 <= i < self._n_len and 0 <= j < self._n_len:
                row = self._g[i]
                if row is not None and j < len(row):
                    e = row[j]
                    if e is not None and isinstance(e, dict):
                        return e
        except (IndexError, TypeError):
            pass
        return {"distance": 99999, "duration": 9999, "transport": "步行"}


def _validate_poi_coords(poi: dict) -> bool:
    """Check that a POI has valid lat/lng coordinates."""
    lat = poi.get("lat")
    lng = poi.get("lng")
    if lat is None or lng is None:
        return False
    try:
        lat_f = float(lat)
        lng_f = float(lng)
        return -90 <= lat_f <= 90 and -180 <= lng_f <= 180
    except (ValueError, TypeError):
        return False


def _haversine_fallback(lat1, lng1, lat2, lng2, mode: str):
    """API 失败时的 haversine 兜底估算（按交通模式调节）."""
    straight = haversine(lat1, lng1, lat2, lng2)
    road_factor = 1.4
    if mode == "步行":
        speed = 1.25          # ~4.5 km/h 步行
    elif mode == "骑行":
        speed = 4.0           # ~14.4 km/h 骑行
    elif mode == "公交/地铁":
        speed = 6.5           # ~23 km/h 含等车/换乘
    else:  # 驾车
        speed = 8.0           # ~29 km/h 城市路况
    dist = int(straight * road_factor)
    dur = int(dist / speed)
    return dist, dur


def pre_prune_pois(pois: list, max_pois: int = 15,
                   anchor_lat=None, anchor_lng=None) -> list:
    """当 POI 过多时预剪枝，保留评分最高的 top-N.

    优先保留靠近起终点的 POI（距离加权）。
    """
    if len(pois) <= max_pois:
        return pois

    scored = []
    for p in pois:
        rating = p.get("rating") or 3.0
        score = rating
        if anchor_lat is not None and anchor_lng is not None:
            d = haversine(anchor_lat, anchor_lng, p.get("lat", 0), p.get("lng", 0))
            # 距离越近加权越高（归一化到 0-5 分）
            dist_bonus = max(0, 5 - d / 2000)
            score = rating + dist_bonus * 0.3
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:max_pois]]


def build_graph(origin: tuple, pois: list, destination: tuple = None):
    """构建节点间全连接加权图.

    优化策略:
    - haversine 距离 > 3000m → 直接估算，不调步行 API
    - 每个节点只对最近 K=8 个邻居调 API
    - 15 POI + 起终点 ≈ 80 次 API 调用，~6s

    Returns:
        nodes: [{"id", "name", "lat", "lng", "type", ...}, ...]
        graph: 邻接矩阵 graph[i][j] = {"distance", "duration", "transport"}
    """
    nodes = [{"id": 0, "name": "起点", "lat": origin[0], "lng": origin[1], "type": "origin"}]

    # Filter POIs with invalid coordinates before building nodes
    valid_pois = [p for p in pois if _validate_poi_coords(p)]
    if len(valid_pois) < len(pois):
        import logging
        logging.getLogger(__name__).warning(
            f"Filtered {len(pois) - len(valid_pois)} POIs with invalid coordinates"
        )
    pois = valid_pois

    for i, p in enumerate(pois):
        nodes.append({
            "id": i + 1,
            "name": p["name"],
            "lat": p["lat"],
            "lng": p["lng"],
            "type": "poi",
            "rating": p.get("rating"),
            "price": p.get("price_per_person"),
            "category": p.get("category", ""),
        })

    has_dest = destination is not None
    if has_dest:
        nodes.append({
            "id": len(nodes),
            "name": "终点",
            "lat": destination[0],
            "lng": destination[1],
            "type": "destination",
        })

    n = len(nodes)
    graph = [[None] * n for _ in range(n)]

    # 阶段1: 预计算所有 haversine 距离和 K 近邻
    straight_dists = {}  # (i, j) → haversine 米
    all_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine(nodes[i]["lat"], nodes[i]["lng"],
                          nodes[j]["lat"], nodes[j]["lng"])
            straight_dists[(i, j)] = d
            all_pairs.append((i, j, d))

    # 阶段2: 决定哪些边需要 API（每节点 K 近邻 + 阈值过滤）
    api_pairs = set()
    for i in range(n):
        neighbors = [(j, straight_dists.get((min(i, j), max(i, j)), float("inf")))
                     for j in range(n) if j != i]
        neighbors.sort(key=lambda x: x[1])
        for j, d in neighbors[:min(_K_NEAREST, len(neighbors))]:
            if d <= _API_DISTANCE_THRESHOLD_M:
                api_pairs.add((min(i, j), max(i, j)))

    # 阶段3: 并行调用真实路线 API（按 haversine 距离自动选模式）
    def _fetch(i, j):
        ni, nj = nodes[i], nodes[j]
        c1, c2 = f"{ni['lng']},{ni['lat']}", f"{nj['lng']},{nj['lat']}"
        dist = haversine(ni["lat"], ni["lng"], nj["lat"], nj["lng"])
        mode = decide_transport(dist)
        try:
            result = get_route(c1, c2, mode=mode)
            if result is not None:
                return result["distance"], result["duration"], result["mode"]
        except Exception:
            pass
        fallback_dist, fallback_dur = _haversine_fallback(ni["lat"], ni["lng"],
                                                          nj["lat"], nj["lng"], mode)
        return fallback_dist, fallback_dur, mode

    if api_pairs:
        with ThreadPoolExecutor(max_workers=10) as pool:
            fut_map = {pool.submit(_fetch, i, j): (i, j) for i, j in api_pairs}
            for f in as_completed(fut_map):
                i, j = fut_map[f]
                dist, dur, transport = f.result()
                edge = {"distance": dist, "duration": dur, "transport": transport}
                graph[i][j] = graph[j][i] = edge

    # 阶段4: 剩余边用 haversine 兜底估算
    for i in range(n):
        for j in range(i + 1, n):
            if graph[i][j] is not None:
                continue
            ni, nj = nodes[i], nodes[j]
            straight = haversine(ni["lat"], ni["lng"], nj["lat"], nj["lng"])
            mode = decide_transport(straight)
            dist, dur = _haversine_fallback(ni["lat"], ni["lng"],
                                            nj["lat"], nj["lng"], mode)
            graph[i][j] = graph[j][i] = {
                "distance": dist,
                "duration": dur,
                "transport": mode,
            }

    return nodes, graph


def _projection_and_perp(lat: float, lng: float, origin: dict, dest: dict) -> tuple:
    """Calculate (projection, perp_dist_km) for a POI relative to OD line.
    
    projection: raw (unclamped) value along OD vector. 0=origin, 1=destination.
    perp_dist_km: perpendicular distance from OD centerline in km.
    """
    olat, olng = origin["lat"], origin["lng"]
    dlat, dlng = dest["lat"], dest["lng"]
    dx = dlat - olat
    dy = dlng - olng
    denom = dx*dx + dy*dy
    if denom < 1e-12:
        return 0.5, 0.0
    # Raw projection (not clamped — preserves spatial ordering)
    t = ((lat - olat)*dx + (lng - olng)*dy) / denom
    # Perpendicular distance (cross product)
    cross = abs((lng - olng)*dx - (lat - olat)*dy)
    perp_deg = cross / math.sqrt(denom)
    perp_km = perp_deg * 111.32 * math.cos(math.radians((olat + dlat)/2))
    return t, perp_km

def _pick_from_segments(items: list, num_stops: int, graph: list) -> list:
    """从排序后的 POI 列表中按分段贪心选取，强制前向约束（不回退）.

    将 items 均分为 num_stops 段，每段取评分最高且品类多样者，500m 内互斥.
    强制前向：每段只能选 projection >= 上一段已选 POI projection 的 item.

    items: [(projection, rating, node_id, category), ...] 已按 projection 排序
    Returns: [node_id, ...]
    """
    selected, picked_cats = [], set()
    last_proj = -float('inf')  # forward-only constraint
    
    for i in range(num_stops):
        lo = i * len(items) // num_stops
        hi = (i + 1) * len(items) // num_stops
        seg = items[lo:hi]
        if not seg:
            continue

        # Safe helper: min distance to already-selected nodes
        def _safe_min_dist(graph, pid, selected_ids):
            if not selected_ids:
                return 0
            min_d = 99999
            for sid in selected_ids:
                try:
                    edge = graph[pid][sid] if pid < len(graph) and sid < len(graph[pid]) else None
                    if edge is not None and isinstance(edge, dict):
                        min_d = min(min_d, edge.get("distance", 99999))
                except (IndexError, TypeError):
                    pass
            return min_d

        # 多目标评分：品类多样 + 评分 + 绕路惩罚 + 前向优先
        def _score(x):
            proj, rating = x[0], x[1]
            cat_bonus = 0 if x[3] in picked_cats else 1.0
            min_dist_to_selected = _safe_min_dist(graph, x[2], selected)
            dev_penalty = max(0, 1 - min_dist_to_selected / 3000) * 0.3
            # Forward preference: bonus for forward progression
            fwd_bonus = min(1.0, max(0, (proj - last_proj) * 2)) if last_proj > -float('inf')/2 else 0
            return rating + cat_bonus * 0.5 - dev_penalty + fwd_bonus * 0.5

        best = None
        best_proj = None
        for proj, rating, pid, cat in sorted(seg, key=_score, reverse=True):
            # 前向约束：只选 projection >= 上一个选中 item 的 projection
            if proj < last_proj - 0.05:
                continue
            too_close = any(
                graph[pid][sid] and graph[pid][sid]["distance"] < 500
                for sid in selected
            )
            if not too_close:
                best, best_proj = pid, proj
                picked_cats.add(cat)
                break
        
        if best is None:
            # 放宽：不检查前向约束，选同段评分最高的
            for proj, rating, pid, cat in sorted(seg, key=lambda x: -x[1]):
                too_close = any(
                    graph[pid][sid] and graph[pid][sid]["distance"] < 500
                    for sid in selected
                )
                if not too_close:
                    best, best_proj = pid, proj
                    picked_cats.add(cat)
                    break
        
        if best is None:
            best = seg[0][2]
            best_proj = seg[0][0]
            picked_cats.add(seg[0][3])
        
        selected.append(best)
        last_proj = best_proj  # update forward constraint
    return selected


def shortest_path(graph: list, nodes: list, num_stops: int,
                  budget_level: str = "medium") -> dict:
    """基于起终点连线投影的分段贪心算法.

    有终点 → 投影分段（POI 投影到起终点连线，均分 num_stops 段）
    无终点 → 距离带分层（按距起点距离分 num_stops 段）
    + 多目标评分：品类多样 + 评分 + 绕路惩罚

    Returns:
        {"node_ids", "segments", "total_duration_min", "total_distance"}
    """
    poi_nodes = [n for n in nodes if n["type"] == "poi"]
    dest_id = next((n["id"] for n in nodes if n["type"] == "destination"), None)
    origin = nodes[0]
    num_stops = min(num_stops, len(poi_nodes))

    if num_stops == 0:
        selected = []
    elif dest_id is not None:
        dest = nodes[dest_id]
        items = []
        for p in poi_nodes:
            t, perp = _projection_and_perp(p["lat"], p["lng"], origin, dest)
            rating = p.get("rating") or 3.0
            # Sort key: weighted by projection position and perpendicular distance
            # Penalize POIs far from the centerline
            sort_key = t - perp * 0.05  # slight penalty for perpendicular deviation
            items.append((sort_key, rating, p["id"], p.get("category", ""), t, perp))
        # First sort by weighted projection for segmentation
        items.sort(key=lambda x: x[0])
        # Then strip to (proj, rating, id, category) for _pick_from_segments
        seg_items = [(x[4], x[1], x[2], x[3]) for x in items]
        selected = _pick_from_segments(seg_items, num_stops, graph)
        # Re-sort selected by raw projection to ensure forward ordering
        selected_projs = {sid: x[4] for sid, x in zip([x[2] for x in seg_items], items) if x[2] in selected}
        selected.sort(key=lambda sid: selected_projs.get(sid, 999))
    else:
        items = []
        for p in poi_nodes:
            e = graph[0][p["id"]]
            d = e["distance"] if e else float("inf")
            items.append((d, p.get("rating") or 3.0, p["id"], p.get("category", "")))
        items.sort(key=lambda x: x[0])
        selected = _pick_from_segments(items, num_stops, graph)

    # 串联路径
    path = [0]
    segments = []
    current = 0

    for pid in selected:
        if not graph[current][pid]:
            continue
        path.append(pid)
        e = graph[current][pid]
        segments.append({
            "from": nodes[current]["name"],
            "to": nodes[pid]["name"],
            "distance": e["distance"],
            "duration": e["duration"],
            "transport": e["transport"],
        })
        current = pid

    if dest_id is not None and dest_id not in path and graph[current][dest_id]:
        path.append(dest_id)
        e = graph[current][dest_id]
        segments.append({
            "from": nodes[current]["name"],
            "to": nodes[dest_id]["name"],
            "distance": e["distance"],
            "duration": e["duration"],
            "transport": e["transport"],
        })

    return {
        "node_ids": path,
        "segments": segments,
        "total_duration_min": round(sum(s["duration"] for s in segments) / 60),
        "total_distance": sum(s["distance"] for s in segments),
    }
