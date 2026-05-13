#!/usr/bin/env python3
"""Seed missing POIs into the local DB for better route coverage.

Targets:
  - Geographic: middle segments of key Xi'an routes (gap between origin/dest clusters)
  - Categories: 酒吧, 书店, 素食, 轻食, 按摩, 景点, 甜品, 西餐, 日料, 小吃

Strategy: search each category at each geo-point via Amap API, dedup, insert.
"""
import sys, time, sqlite3, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.providers.amap_provider import search_poi
from app.algorithms.geo import haversine

# ========== Configuration ==========
DB_PATH = "db/poi.db"

# Categories to search (keyword, amap_search_term)
CATEGORIES = [
    ("酒吧", "酒吧"),
    ("书店", "书店"),
    ("素食", "素食餐厅"),
    ("轻食", "轻食沙拉"),
    ("按摩", "按摩"),
    ("甜品", "甜品店"),
    ("西餐", "西餐厅"),
    ("日料", "日本料理"),
    ("小吃", "特色小吃"),
]

# Search points: middle segments of key routes
# Format: (name, lat, lng, radius_km)
SEARCH_POINTS = [
    # 钟楼→大雁塔 中间段
    ("钟楼南_南大街", 34.248, 108.946, 1.5),
    ("南稍门", 34.242, 108.948, 1.5),
    ("体育场", 34.234, 108.950, 1.5),
    ("小寨北", 34.230, 108.946, 1.5),
    ("大雁塔北_翠华路", 34.226, 108.954, 1.5),
    # 钟楼→高新 中间段
    ("西关正街", 34.255, 108.920, 1.5),
    ("丰庆路", 34.248, 108.910, 1.5),
    ("高新路中段", 34.240, 108.895, 1.5),
    # 钟楼→浐灞 中间段  
    ("朝阳门", 34.268, 108.965, 1.5),
    ("胡家庙", 34.272, 108.985, 1.5),
    ("辛家庙", 34.290, 108.998, 1.5),
    # 钟楼→火车站北
    ("北大街", 34.270, 108.948, 1.5),
    ("龙首原", 34.290, 108.955, 1.5),
    # 大雁塔附近补充
    ("大雁塔周边", 34.215, 108.960, 1.5),
    ("大唐不夜城南", 34.208, 108.965, 1.5),
]

def insert_poi(conn, poi: dict) -> bool:
    """Insert POI into DB, skip if duplicate name+lat+lng."""
    name = poi.get("name", "")
    lat = poi.get("lat")
    lng = poi.get("lng")
    if not name or lat is None or lng is None:
        return False
    
    # Check existing
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM pois WHERE name=? AND ABS(lat-?)<0.0001 AND ABS(lng-?)<0.0001",
        (name, lat, lng)
    )
    if cur.fetchone():
        return False
    
    # search_poi returns: {amap_id, name, address, category, lat, lng, rating, price_per_person, distance}
    amap_id = poi.get("amap_id", "")
    category = poi.get("category", "")  # Amap 'type' taxonomy
    subcategory = category  # Use same taxonomy for subcategory
    rating = poi.get("rating")
    price = poi.get("price_per_person")
    address = poi.get("address", "")
    
    try:
        cur.execute("""
            INSERT INTO pois (amap_id, name, lat, lng, category, subcategory, rating, price_per_person, address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            amap_id or "", name, float(lat), float(lng), 
            category or "", subcategory or "",
            float(rating) if rating else None,
            float(price) if price else None,
            address or ""
        ))
        return True
    except Exception as e:
        print(f"  ⚠️ insert error: {e}")
        return False

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    
    total_before = conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
    total_inserted = 0
    
    for cat_name, search_kw in CATEGORIES:
        cat_inserted = 0
        print(f"\n{'='*50}")
        print(f"🔍 品类: {cat_name} (搜索词: {search_kw})")
        
        for pt_name, lat, lng, radius in SEARCH_POINTS:
            try:
                results = search_poi(
                    keywords=search_kw,
                    location=f"{lng},{lat}",
                    radius_km=radius,
                    limit=10,
                )
            except Exception as e:
                print(f"  ⚠️ API error at {pt_name}: {e}")
                continue
            
            inserted = 0
            for poi in results:
                if insert_poi(conn, poi):
                    inserted += 1
            
            if inserted:
                print(f"  {pt_name}: +{inserted}")
            cat_inserted += inserted
            conn.commit()
            time.sleep(0.15)  # Rate limit
        
        print(f"  {cat_name} 总计: +{cat_inserted}")
        total_inserted += cat_inserted
    
    total_after = conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
    print(f"\n{'='*50}")
    print(f"✅ 完成: {total_before} → {total_after} (+{total_inserted})")
    conn.close()

if __name__ == "__main__":
    main()
