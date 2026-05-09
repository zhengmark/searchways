"""方案六: 用高德 API 批量补充缺失品类 POI 到本地 DB."""
import sys, time, sqlite3
sys.path.insert(0, '.')
from pathlib import Path
from app.providers.amap_provider import search_poi
from db.connection import get_conn

# 缺失品类 × 搜索关键词
MISSING_CATEGORIES = {
    "轻食沙拉": ["轻食", "沙拉", "健康餐", "健身餐"],
    "素食": ["素食", "素食餐厅", "斋菜"],
    "商务宴请": ["商务餐厅", "宴会厅", "私房菜", "包间"],
    "按摩SPA": ["按摩", "SPA", "足疗", "推拿"],
    "书店": ["书店", "独立书店", "书吧"],
    "花店": ["花店", "鲜花"],
    "茶馆": ["茶馆", "茶社", "茶室", "棋牌茶"],
    "咖啡": ["精品咖啡", "独立咖啡", "手冲咖啡"],
    "甜品": ["甜品店", "蛋糕店", "冰淇淋", "糖水"],
    "博物馆": ["博物馆", "展览馆", "美术馆", "画廊"],
}

# 西安各区中心坐标（确保覆盖）
XI_AN_CENTERS = [
    ("钟楼", 34.260, 108.942),
    ("小寨", 34.225, 108.942),
    ("高新", 34.233, 108.886),
    ("曲江", 34.203, 108.990),
    ("北站", 34.379, 108.939),
    ("东郊", 34.268, 109.015),
    ("西郊", 34.265, 108.850),
    ("长安", 34.158, 108.907),
]


def seed_once(category, keywords, lat, lng, cursor):
    """搜一次，写入新 POI."""
    new_count = 0
    for kw in keywords[:2]:  # 每个品类只搜前2个关键词（省配额）
        try:
            pois = search_poi(keywords=kw, city="西安",
                              location=f"{lng},{lat}", radius=5000, offset=15)
            for p in pois:
                name = p.get("name", "")
                amap_id = p.get("id", "")
                if not amap_id:
                    continue
                # 检查是否已存在
                existing = cursor.execute(
                    "SELECT id FROM pois WHERE amap_id = ?", (amap_id,)
                ).fetchone()
                if existing:
                    continue
                cursor.execute("""
                    INSERT INTO pois (amap_id, name, address, category, subcategory,
                                     lat, lng, rating, price_per_person, city)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '西安')
                """, (
                    amap_id,
                    name,
                    p.get("address", ""),
                    category,
                    p.get("type", ""),
                    p.get("lat"),
                    p.get("lng"),
                    p.get("rating"),
                    p.get("price_per_person"),
                ))
                new_count += 1
            time.sleep(0.3)  # 避免冲垮 API
        except Exception as e:
            print(f"    ⚠️ {kw} @ ({lat:.2f},{lng:.2f}): {e}")
    return new_count


if __name__ == "__main__":
    print("🌱 批量补充缺失品类 POI")
    db_path = Path(__file__).parent.parent / "db" / "poi.db"
    total_new = 0

    with get_conn(str(db_path)) as conn:
        cur = conn.cursor()

        for category, keywords in MISSING_CATEGORIES.items():
            cat_total = 0
            print(f"\n▶ {category} ({keywords[:3]})")
            for name, lat, lng in XI_AN_CENTERS[:4]:  # 前4个中心覆盖主城区
                n = seed_once(category, keywords, lat, lng, cur)
                if n:
                    conn.commit()
                    cat_total += n
                    print(f"    {name}: +{n}")
                time.sleep(0.5)

            total_new += cat_total
            if cat_total >= 20:
                print(f"  ✅ {category}: +{cat_total} (够了)")
            elif cat_total > 0:
                print(f"  ⚠️ {category}: +{cat_total} (偏少)")
            else:
                print(f"  ❌ {category}: +0 (未找到)")
            time.sleep(1)

    print(f"\n{'='*40}")
    print(f"🎉 总计新增 {total_new} 条 POI")
    print(f"💡 建议跑一次 python3 db/maintenance.py --recluster 重新聚类")
