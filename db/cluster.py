"""聚类预计算 — 网格法对全量 POI 做地理簇标记，缓存到 DB.

用法:
    python3 -m db.cluster build           # 全量重算聚类
    python3 -m db.cluster stats           # 查看聚类统计
    python3 -m db.cluster query 34.26 108.94  # 查询坐标附近簇

网格法: 0.01°×0.01°（约 1km），每格 ≥3 POI 即为一个簇。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.connection import get_conn


def _cell_key(lat: float, lng: float, resolution: float = 0.01) -> str:
    """计算 POI 所属网格 key."""
    col = int(lng / resolution)
    row = int(lat / resolution)
    return f"{row},{col}"


def _cell_center(key: str, resolution: float = 0.01) -> tuple:
    """从网格 key 计算中心坐标."""
    row, col = key.split(",")
    return (int(row) + 0.5) * resolution, (int(col) + 0.5) * resolution


def build_clusters(min_samples: int = 3, resolution: float = 0.01):
    """全量重算聚类：按网格分组 → 每个满员格为一个簇."""
    with get_conn() as conn:
        # 重置
        conn.execute("UPDATE pois SET cluster_id = NULL")

        # 加载所有有坐标的 POI
        rows = conn.execute(
            "SELECT id, lat, lng FROM pois WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()
        print(f"加载 {len(rows)} 条 POI")

        # 按网格分组
        cells = {}
        for r in rows:
            key = _cell_key(r["lat"], r["lng"], resolution)
            cells.setdefault(key, []).append(r["id"])

        # 满员格 → 簇
        cluster_id = 0
        total_assigned = 0
        for key, ids in sorted(cells.items()):
            if len(ids) >= min_samples:
                cluster_id += 1
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE pois SET cluster_id = ? WHERE id IN ({placeholders})",
                    [cluster_id] + ids,
                )
                total_assigned += len(ids)

        conn.commit()
        print(f"完成：{cluster_id} 个簇，覆盖 {total_assigned} 条 POI ({total_assigned/len(rows)*100:.1f}%)")

        # 保存簇元数据
        conn.execute("DROP TABLE IF EXISTS cluster_meta")
        conn.execute("""
            CREATE TABLE cluster_meta (
                cluster_id INTEGER PRIMARY KEY,
                center_lat REAL NOT NULL,
                center_lng REAL NOT NULL,
                size INTEGER NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO cluster_meta (cluster_id, center_lat, center_lng, size)
            SELECT cluster_id, AVG(lat), AVG(lng), COUNT(*)
            FROM pois WHERE cluster_id IS NOT NULL
            GROUP BY cluster_id
        """)
        conn.commit()


def query_clusters(lat: float, lng: float, limit: int = 10) -> list:
    """查询距离坐标最近的几个簇."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT cluster_id, center_lat, center_lng, size,
                   ((center_lat - ?) * (center_lat - ?) + (center_lng - ?) * (center_lng - ?)) AS dist2
            FROM cluster_meta
            ORDER BY dist2 ASC
            LIMIT ?
        """, (lat, lat, lng, lng, limit)).fetchall()
        return [dict(r) for r in rows]


def cluster_stats():
    """输出聚类统计."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
        clustered = conn.execute(
            "SELECT COUNT(*) FROM pois WHERE cluster_id IS NOT NULL"
        ).fetchone()[0]
        n_clusters = conn.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM pois WHERE cluster_id IS NOT NULL"
        ).fetchone()[0]
        # 簇大小分布
        sizes = conn.execute("""
            SELECT cluster_id, COUNT(*) as sz FROM pois
            WHERE cluster_id IS NOT NULL GROUP BY cluster_id ORDER BY sz DESC
        """).fetchall()

        print(f"总 POI: {total}")
        print(f"已聚类: {clustered} ({clustered/total*100:.1f}%)")
        print(f"簇数量: {n_clusters}")
        if sizes:
            print(f"最大簇: {sizes[0][1]} POI (cluster_id={sizes[0][0]})")
            print(f"最小簇: {sizes[-1][1]} POI (cluster_id={sizes[-1][0]})")
            avg = sum(s[1] for s in sizes) / len(sizes)
            print(f"平均簇大小: {avg:.1f}")
            # 大小分布
            buckets = {"3-5": 0, "6-10": 0, "11-20": 0, "21-50": 0, "51+": 0}
            for _, sz in sizes:
                if sz <= 5: buckets["3-5"] += 1
                elif sz <= 10: buckets["6-10"] += 1
                elif sz <= 20: buckets["11-20"] += 1
                elif sz <= 50: buckets["21-50"] += 1
                else: buckets["51+"] += 1
            print(f"大小分布: {buckets}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "build":
        build_clusters()
    elif cmd == "stats":
        cluster_stats()
    elif cmd == "query" and len(sys.argv) >= 4:
        lat, lng = float(sys.argv[2]), float(sys.argv[3])
        for c in query_clusters(lat, lng):
            print(f"  cluster_id={c['cluster_id']} size={c['size']} center=({c['center_lat']:.4f},{c['center_lng']:.4f})")
    else:
        print(__doc__)
