"""从高德 API 拉取西安 POI 数据入库（SQLite）.

网格扫描策略：将西安划分为 ~0.04° 网格，每个格点用 search_around
搜索 5 大品类，按 amap_id 去重写入 SQLite。

用法：
    python db/seed.py              # 默认网格 0.04°
    python db/seed.py --step 0.03  # 更密网格
    python db/seed.py --dry-run    # 仅统计不写入
"""
import argparse
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.providers.amap_provider import search_around
from db.connection import get_conn, init_db

# 西安边界
XIAN_LNG_MIN, XIAN_LNG_MAX = 108.70, 109.20
XIAN_LAT_MIN, XIAN_LAT_MAX = 34.10, 34.50

# 搜索品类 → 高德关键词
CATEGORIES = {
    "餐饮": ["美食", "火锅", "咖啡", "小吃"],
    "购物": ["商场", "购物", "超市"],
    "景点": ["景点", "公园", "博物馆", "寺庙"],
    "休闲": ["娱乐", "KTV", "影院"],
    "文化": ["图书馆", "美术馆", "剧院"],
}

# 高德 type → 大类映射（基于常见 type 前缀）
TYPE_CATEGORY = {
    "餐饮": "餐饮", "中餐": "餐饮", "火锅": "餐饮", "小吃": "餐饮", "烧烤": "餐饮",
    "咖啡": "餐饮", "西餐": "餐饮", "日料": "餐饮", "甜点": "餐饮", "茶饮": "餐饮",
    "面包": "餐饮", "冷饮": "餐饮", "海鲜": "餐饮", "自助": "餐饮", "面馆": "餐饮",
    "购物": "购物", "商场": "购物", "超市": "购物", "市场": "购物", "商业街": "购物",
    "公园": "景点", "风景": "景点", "寺庙": "景点", "博物馆": "景点", "故居": "景点",
    "遗址": "景点", "园林": "景点", "古镇": "景点", "教堂": "景点", "塔": "景点",
    "休闲": "休闲", "娱乐": "休闲", "KTV": "休闲", "影院": "休闲", "酒吧": "休闲",
    "足疗": "休闲", "桌游": "休闲", "密室": "休闲", "洗浴": "休闲", "运动": "休闲",
    "健身": "休闲", "游泳": "休闲", "滑雪": "休闲",
    "文化": "文化", "图书馆": "文化", "美术馆": "文化", "剧院": "文化", "音乐厅": "文化",
    "文化宫": "文化", "科技馆": "文化", "展览": "文化",
    "酒店": "酒店", "宾馆": "酒店", "旅店": "酒店", "民宿": "酒店",
    "交通": "交通", "地铁": "交通", "公交": "交通", "火车站": "交通", "机场": "交通",
}


def classify_category(subcategory: str) -> str:
    """根据高德 type 推断大类."""
    if not subcategory:
        return "其他"
    for key, cat in TYPE_CATEGORY.items():
        if key in subcategory:
            return cat
    # 反向匹配：subcategory 出现在 key 中（如"国家级景点"含"景点"）
    for key, cat in TYPE_CATEGORY.items():
        if subcategory in key:
            return cat
    return "其他"


def generate_grid(step: float = 0.04) -> list:
    """生成西安网格中心点列表."""
    grid = []
    lat = XIAN_LAT_MIN
    while lat <= XIAN_LAT_MAX:
        lng = XIAN_LNG_MIN
        while lng <= XIAN_LNG_MAX:
            grid.append((lng, lat))
            lng += step
        lat += step
    return grid


def seed(step: float = 0.04, dry_run: bool = False, delay: float = 0.06):
    """主入库流程."""
    grid = generate_grid(step)
    total_kw = sum(len(kws) for kws in CATEGORIES.values())
    total_queries = len(grid) * total_kw
    print(f"网格：{len(grid)} 点（步长 {step}°）")
    print(f"品类：{len(CATEGORIES)} 类")
    print(f"预计 API 调用：{total_queries} 次")
    print(f"预计耗时：{total_queries * delay:.0f} 秒")

    if dry_run:
        print("[DRY RUN] 跳过实际调用")
        return

    # 初始化 DB
    init_db()
    print("数据库已初始化")

    # 收集所有 POI（内存中按 amap_id 去重）
    seen_ids = set()
    all_pois = []
    calls = 0

    for i, (lng, lat) in enumerate(grid):
        loc = f"{lng:.6f},{lat:.6f}"
        for cat_name, keywords in CATEGORIES.items():
            for kw in keywords:
                calls += 1
                try:
                    pois = search_around(loc, kw, radius=5000, limit=25)
                    for p in pois:
                        aid = p.get("amap_id", "")
                        if aid and aid not in seen_ids:
                            seen_ids.add(aid)
                            raw_type = p.get("category", "")
                            p["subcategory"] = raw_type
                            p["category"] = classify_category(raw_type)
                            all_pois.append(p)
                except Exception as e:
                    print(f"  ⚠ [{calls}/{total_queries}] {loc} {kw}: {e}")
                time.sleep(delay)

        if (i + 1) % 10 == 0:
            print(f"  进度：{i + 1}/{len(grid)} 网格点，已收集 {len(all_pois)} POI（{calls} 次调用）")

    print(f"\n搜索完成：{calls} 次 API 调用，去重后 {len(all_pois)} 个 POI")

    # 批量写入
    with get_conn() as conn:
        inserted = 0
        for p in all_pois:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO pois
                       (amap_id, name, address, category, subcategory, lat, lng, rating, price_per_person, city, district)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        p.get("amap_id") or "",
                        p.get("name") or "",
                        p.get("address") or "",
                        p.get("category", ""),
                        p.get("subcategory", ""),
                        p.get("lat"),
                        p.get("lng"),
                        p.get("rating"),
                        p.get("price_per_person"),
                        "西安",
                        "",
                    ),
                )
                inserted += 1
            except Exception as e:
                print(f"  ⚠ 写入失败 {p.get('name', '?')}: {e}")

    print(f"写入完成：{inserted} 条（跳过 {len(all_pois) - inserted} 条重复）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="西安 POI 数据入库")
    parser.add_argument("--step", type=float, default=0.04, help="网格步长（度），默认 0.04")
    parser.add_argument("--dry-run", action="store_true", help="仅统计不写入")
    parser.add_argument("--delay", type=float, default=0.06, help="API 调用间隔（秒），默认 0.06")
    args = parser.parse_args()
    seed(step=args.step, dry_run=args.dry_run, delay=args.delay)
