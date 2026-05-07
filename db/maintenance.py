"""POI 数据维护 — 增量更新评分/价格，新 POI 发现.

用法:
    python3 -m db.maintenance update    # 更新评分/价格（每次 50 条）
    python3 -m db.maintenance update --all  # 全量更新
    python3 -m db.maintenance stats     # 查看统计
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.connection import get_conn
from app.providers.amap_provider import batch_get_poi_details


def _needs_update(conn, limit: int = 50) -> list:
    """找出需要更新的 POI（rating 为空 或 超过7天未更新）."""
    rows = conn.execute("""
        SELECT amap_id, name, rating, price_per_person, updated_at
        FROM pois
        WHERE rating IS NULL
           OR updated_at IS NULL
           OR updated_at < datetime('now', '-7 days')
        ORDER BY rating IS NULL DESC, updated_at ASC NULLS FIRST
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def run_update(limit: int = 50):
    """执行增量更新."""
    with get_conn() as conn:
        rows = _needs_update(conn, limit)
        if not rows:
            print("所有 POI 数据已是最新，无需更新。")
            return

        print(f"找到 {len(rows)} 条待更新 POI，正在查询详情...")
        ids = [r["amap_id"] for r in rows]
        details = batch_get_poi_details(ids)

        updated = 0
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for r in rows:
            detail = details.get(r["amap_id"])
            if not detail:
                continue
            rating = detail.get("rating")
            price = detail.get("price_per_person")
            if rating is None and price is None:
                continue
            conn.execute("""
                UPDATE pois SET rating = ?, price_per_person = ?, updated_at = ?
                WHERE amap_id = ?
            """, (rating, price, now, r["amap_id"]))
            updated += 1

        conn.commit()
        print(f"完成：更新 {updated}/{len(rows)} 条 POI。")


def run_stats():
    """输出数据库统计."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
        with_rating = conn.execute(
            "SELECT COUNT(*) FROM pois WHERE rating IS NOT NULL"
        ).fetchone()[0]
        stale = conn.execute(
            "SELECT COUNT(*) FROM pois WHERE updated_at IS NULL OR updated_at < datetime('now', '-7 days')"
        ).fetchone()[0]
        by_cat = conn.execute(
            "SELECT category, COUNT(*) FROM pois GROUP BY category ORDER BY COUNT(*) DESC"
        ).fetchall()
        print(f"总 POI 数：{total}")
        print(f"有评分：{with_rating} / {total}")
        print(f"待更新（7天+）：{stale}")
        print(f"按品类：")
        for r in by_cat:
            print(f"  {r[0]}: {r[1]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    limit = 50
    if "--all" in sys.argv:
        limit = 5000
    elif any(a.startswith("--limit=") for a in sys.argv):
        limit = int([a for a in sys.argv if a.startswith("--limit=")][0].split("=")[1])

    if cmd == "update":
        run_update(limit)
    elif cmd == "stats":
        run_stats()
    else:
        print(__doc__)
