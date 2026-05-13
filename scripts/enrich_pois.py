"""补搜缺失品类POI并重新聚类."""
import sys, time, sqlite3
sys.path.insert(0, '.')
from app.providers.amap_provider import search_poi, AmapAPIError

MISSING_CATS = {
    "素食": "餐饮", "轻食": "餐饮", "沙拉": "餐饮", "健康餐": "餐饮",
    "健身房": "运动健身", "SPA": "休闲", "按摩": "休闲", "茶馆": "茶饮",
    "书店": "文化", "密室逃脱": "娱乐", "剧本杀": "娱乐", "Livehouse": "娱乐",
    "甜品": "餐饮", "冰淇淋": "餐饮", "日料": "餐饮", "西餐": "餐饮",
    "海鲜": "餐饮", "粤菜": "餐饮", "烧烤": "餐饮",
}

AREAS = [
    ("钟楼", "108.947,34.261"), ("小寨", "108.947,34.224"),
    ("高新", "108.886,34.196"), ("曲江", "108.996,34.210"),
    ("北郊", "108.946,34.333"), ("浐灞", "109.043,34.329"),
    ("长安", "108.914,34.158"), ("经开", "108.940,34.347"),
    ("电视塔", "108.940,34.195"), ("纺织城", "109.068,34.272"),
]

conn = sqlite3.connect("db/poi.db")
conn.execute("PRAGMA journal_mode=WAL")
total_added, total_skipped = 0, 0

for cat_name, cat_type in MISSING_CATS.items():
    for area_name, coord in AREAS:
        try:
            pois = search_poi(keywords=cat_name, location=coord, radius_km=5, limit=15)
            for p in pois:
                name = p.get("name", "")
                lat = p.get("lat")
                lng = p.get("lng")
                if lat is None or lng is None:
                    continue
                existing = conn.execute(
                    "SELECT id FROM pois WHERE name = ? AND ABS(lat - ?) < 0.001 AND ABS(lng - ?) < 0.001",
                    (name, lat, lng)
                ).fetchone()
                if existing:
                    total_skipped += 1
                    continue
                conn.execute("""
                    INSERT INTO pois (name, category, subcategory, lat, lng, 
                                    rating, price_per_person, address, district, type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, p.get("category", cat_type), p.get("category", cat_type),
                      lat, lng, p.get("rating"), p.get("price_per_person"),
                      p.get("address", ""), p.get("district", area_name), cat_type))
                total_added += 1
            time.sleep(0.12)
        except Exception as e:
            time.sleep(0.3)
    conn.commit()
    print(f"  {cat_name}: done (total +{total_added}, skip {total_skipped})", flush=True)

conn.commit()
conn.close()
print(f"\n✅ Added: {total_added}, Skipped: {total_skipped}")
