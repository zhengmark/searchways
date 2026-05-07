"""图路线规划引擎 — 加权图建模 + 投影分段路径选取."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.algorithms.routing import walk_distance
from app.algorithms.geo import haversine, project_ratio

# 步行 API 只在短距离节点间调用；远距离直接用 haversine 估算
_API_DISTANCE_THRESHOLD_M = 3000
# 每个节点只对最近的 K 个邻居调用步行 API
_K_NEAREST = 8

_SPEED_FACTOR = {"步行": 1.0, "骑行": 3.0, "公交/地铁": 2.0, "打车": 5.0}


def decide_transport(distance_meters: int) -> str:
    if distance_meters < 800:
        return "步行"
    if distance_meters <= 3000:
        return "骑行"
    if distance_meters <= 8000:
        return "公交/地铁"
    return "打车"


def _haversine_estimate(lat1, lng1, lat2, lng2):
    """用 haversine 直线距离估算步行距离和耗时."""
    straight = haversine(lat1, lng1, lat2, lng2)
    return int(straight * 1.4), int(straight * 1.4 / 1.3)


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

    # 阶段3: 并行调用步行 API（仅 api_pairs 中的边）
    def _fetch(i, j):
        ni, nj = nodes[i], nodes[j]
        c1, c2 = f"{ni['lng']},{ni['lat']}", f"{nj['lng']},{nj['lat']}"
        walk_dist = walk_dur = None
        try:
            result = walk_distance(c1, c2)
            if result:
                walk_dist, walk_dur = result["distance"], result["duration"]
        except Exception:
            pass
        if walk_dist is None:
            walk_dist, walk_dur = _haversine_estimate(ni["lat"], ni["lng"],
                                                       nj["lat"], nj["lng"])
        transport = decide_transport(walk_dist)
        factor = _SPEED_FACTOR.get(transport, 1.0)
        return walk_dist, int(walk_dur / factor), transport

    if api_pairs:
        with ThreadPoolExecutor(max_workers=10) as pool:
            fut_map = {pool.submit(_fetch, i, j): (i, j) for i, j in api_pairs}
            for f in as_completed(fut_map):
                i, j = fut_map[f]
                dist, dur, transport = f.result()
                edge = {"distance": dist, "duration": dur, "transport": transport}
                graph[i][j] = graph[j][i] = edge

    # 阶段4: 剩余边用 haversine 估算
    for i in range(n):
        for j in range(i + 1, n):
            if graph[i][j] is not None:
                continue
            ni, nj = nodes[i], nodes[j]
            walk_dist, walk_dur = _haversine_estimate(ni["lat"], ni["lng"],
                                                       nj["lat"], nj["lng"])
            transport = decide_transport(walk_dist)
            factor = _SPEED_FACTOR.get(transport, 1.0)
            graph[i][j] = graph[j][i] = {
                "distance": walk_dist,
                "duration": int(walk_dur / factor),
                "transport": transport,
            }

    return nodes, graph


def _pick_from_segments(items: list, num_stops: int, graph: list) -> list:
    """从排序后的 POI 列表中按分段贪心选取.

    将 items 均分为 num_stops 段，每段取评分最高且品类多样者，500m 内互斥.

    items: [(sort_key, rating, node_id, category), ...] 已按 sort_key 排序
    Returns: [node_id, ...]
    """
    selected, picked_cats = [], set()
    for i in range(num_stops):
        lo = i * len(items) // num_stops
        hi = (i + 1) * len(items) // num_stops
        seg = items[lo:hi]
        if not seg:
            continue

        # 多目标评分：品类多样 + 评分 + 绕路惩罚（到已选节点的最小距离）
        def _score(x):
            cat_bonus = 0 if x[3] in picked_cats else 1.0
            rating = x[1]
            # 绕路惩罚：距离已选节点越近越好
            min_dist_to_selected = min(
                (graph[x[2]][sid]["distance"] if graph[x[2]][sid] else 99999)
                for sid in selected
            ) if selected else 0
            dev_penalty = max(0, 1 - min_dist_to_selected / 3000) * 0.3
            return rating + cat_bonus * 0.5 - dev_penalty

        best = None
        for _, _, pid, cat in sorted(seg, key=_score, reverse=True):
            too_close = any(
                graph[pid][sid] and graph[pid][sid]["distance"] < 500
                for sid in selected
            )
            if not too_close:
                best = pid
                picked_cats.add(cat)
                break
        if best is None:
            best = seg[0][2]
            picked_cats.add(seg[0][3])
        selected.append(best)
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
            t = project_ratio(p["lat"], p["lng"], origin, dest)
            rating = p.get("rating") or 3.0
            items.append((t, rating, p["id"], p.get("category", "")))
        items.sort(key=lambda x: x[0])
        selected = _pick_from_segments(items, num_stops, graph)
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
