"""图路线规划引擎 — 加权图建模 + 投影分段路径选取."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent.tools.routing import walk_distance
from agent.tools.geo import haversine, project_ratio


def decide_transport(distance_meters: int) -> str:
    """根据距离推荐交通工具."""
    if distance_meters < 800:
        return "步行"
    if distance_meters <= 3000:
        return "骑行"
    if distance_meters <= 8000:
        return "公交/地铁"
    return "打车"


def build_graph(origin: tuple, pois: list, destination: tuple = None):
    """构建节点间全连接加权图.

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
            straight = haversine(ni["lat"], ni["lng"], nj["lat"], nj["lng"])
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


def shortest_path(graph: list, nodes: list, num_stops: int) -> dict:
    """基于起终点连线投影的分段贪心算法.

    将 POI 投影到起终点连线上 → 均分 num_stops 段 → 每段取评分最高者
    → 串联：起点 → POI₁ → POI₂ → ... → 终点
    保证空间均匀分布，无回溯.

    Returns:
        {"node_ids", "segments", "total_duration_min", "total_distance"}
    """
    poi_nodes = [n for n in nodes if n["type"] == "poi"]
    dest_id = next((n["id"] for n in nodes if n["type"] == "destination"), None)
    origin = nodes[0]
    num_stops = min(num_stops, len(poi_nodes))

    if dest_id is not None and num_stops > 0:
        dest = nodes[dest_id]
        scored = []
        for p in poi_nodes:
            t = project_ratio(p["lat"], p["lng"], origin, dest)
            rating = p.get("rating") or 3.0
            scored.append((t, rating, p["id"]))

        scored.sort(key=lambda x: x[0])

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
        if num_stops == 0:
            selected = []
        else:
            dists = []
            for p in poi_nodes:
                e = graph[0][p["id"]]
                d = e["distance"] if e else float("inf")
                dists.append((d, p.get("rating") or 3.0, p["id"]))
            dists.sort(key=lambda x: x[0])

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
