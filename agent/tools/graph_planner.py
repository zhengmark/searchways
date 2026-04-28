"""
图路线规划引擎

将起终点 + POI 建模为加权图，用贪心算法计算最优路径。
每段距离优先调高德步行 API，失败时用 haversine 估算兜底。
"""

import math
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent.tools.routing import walk_distance


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """两点间直线距离（米）"""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return int(2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def decide_transport(distance_meters: int) -> str:
    """根据距离推荐交通工具"""
    if distance_meters < 800:
        return "步行"
    if distance_meters <= 3000:
        return "骑行"
    if distance_meters <= 8000:
        return "公交/地铁"
    return "打车"


def build_graph(origin: tuple, pois: list, destination: tuple = None):
    """
    构建节点间距离图。

    参数:
        origin: (lat, lng)
        pois: [{"name": str, "lat": float, "lng": float, ...}, ...]
        destination: (lat, lng) 或 None（路径终点取最后一个 POI）

    返回:
        nodes: 节点列表 [{"id", "name", "lat", "lng", "type", ...}, ...]
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
    tasks = [(i, j) for i in range(n) for j in range(i + 1, n)]

    # 交通工具速度系数（相对步行 5km/h）
    _SPEED_FACTOR = {"步行": 1.0, "骑行": 3.0, "公交/地铁": 2.0, "打车": 5.0}

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
            straight = _haversine(ni["lat"], ni["lng"], nj["lat"], nj["lng"])
            walk_dist, walk_dur = int(straight * 1.4), int(straight * 1.4 / 1.3)
        transport = decide_transport(walk_dist)
        factor = _SPEED_FACTOR.get(transport, 1.0)
        return walk_dist, int(walk_dur / factor), transport

    with ThreadPoolExecutor(max_workers=10) as pool:
        fut_map = {pool.submit(_fetch, i, j): (i, j) for i, j in tasks}
        for f in as_completed(fut_map):
            i, j = fut_map[f]
            dist, dur, transport = f.result()
            edge = {"distance": dist, "duration": dur, "transport": transport}
            graph[i][j] = graph[j][i] = edge

    return nodes, graph


def _project_ratio(lat: float, lng: float, origin: dict, dest: dict) -> float:
    """将 POI 投影到起点→终点连线上，返回 [0,1] 比例（0=起点, 1=终点）。"""
    dx = dest["lng"] - origin["lng"]
    dy = dest["lat"] - origin["lat"]
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        return 0.5
    t = ((lng - origin["lng"]) * dx + (lat - origin["lat"]) * dy) / denom
    return max(0.0, min(1.0, t))


def shortest_path(graph: list, nodes: list, num_stops: int):
    """
    基于起终点连线投影的分段贪心算法：
    - 将各 POI 投影到起终点连线上，得到 [0,1] 比例
    - 均分 num_stops 段，每段选评分最高的 POI
    - 按顺序串联：起点 → POI₁ → POI₂ → ... → 终点
    - 保证 POI 空间上自然分散，无回溯

    返回:
        {"node_ids", "segments", "total_duration_min", "total_distance"}
    """
    poi_nodes = [n for n in nodes if n["type"] == "poi"]
    dest_id = next((n["id"] for n in nodes if n["type"] == "destination"), None)
    origin = nodes[0]
    num_stops = min(num_stops, len(poi_nodes))

    if dest_id is not None and num_stops > 0:
        dest = nodes[dest_id]
        # 每个 POI 计算投影比例
        scored = []
        for p in poi_nodes:
            t = _project_ratio(p["lat"], p["lng"], origin, dest)
            rating = p.get("rating") or 3.0
            scored.append((t, rating, p["id"]))

        scored.sort(key=lambda x: x[0])  # 沿前进方向排序

        # 均分为 num_stops 段，每段取评分最高者（跳过与已选 POI 过近的）
        selected = []
        for i in range(num_stops):
            lo = i * len(scored) // num_stops
            hi = (i + 1) * len(scored) // num_stops
            seg = scored[lo:hi]
            if not seg:
                continue
            seg_sorted = sorted(seg, key=lambda x: -x[1])
            best = None
            for _, _, pid in seg_sorted:
                too_close = any(
                    graph[pid][sid] and graph[pid][sid]["distance"] < 200
                    for sid in selected
                )
                if not too_close:
                    best = pid
                    break
            if best is None:
                best = seg_sorted[0][2]
            selected.append(best)
    else:
        # 无终点：按距起点距离分层，每层选评分最高，确保空间分散
        if num_stops == 0:
            selected = []
        else:
            dists = []
            for p in poi_nodes:
                e = graph[0][p["id"]]
                d = e["distance"] if e else float("inf")
                dists.append((d, p.get("rating") or 3.0, p["id"]))
            dists.sort(key=lambda x: x[0])  # 按距离从近到远排序

            # 均分 num_stops 个距离带，每带取评分最高（跳过过近的）
            selected = []
            for i in range(num_stops):
                lo = i * len(dists) // num_stops
                hi = (i + 1) * len(dists) // num_stops
                seg = dists[lo:hi]
                if not seg:
                    continue
                seg_sorted = sorted(seg, key=lambda x: -x[1])
                best = None
                for _, _, pid in seg_sorted:
                    too_close = any(
                        graph[pid][sid] and graph[pid][sid]["distance"] < 200
                        for sid in selected
                    )
                    if not too_close:
                        best = pid
                        break
                if best is None:
                    best = seg_sorted[0][2]
                selected.append(best)

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

    # 终点段
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
