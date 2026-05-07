-- SQLite POI 数据库（西安限定）
-- 空间查询用 Haversine 手算，不依赖 PostGIS

CREATE TABLE IF NOT EXISTS pois (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amap_id TEXT UNIQUE NOT NULL,        -- 高德内部 ID，去重 + 增量更新关键
    name TEXT NOT NULL,
    address TEXT DEFAULT '',
    category TEXT DEFAULT '',            -- 大类（从 subcategory 推断：餐饮/购物/景点/休闲/文化/酒店/交通）
    subcategory TEXT DEFAULT '',         -- 高德原始 type 字段（如"中餐厅""火锅""公园"）
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    rating REAL,                         -- biz_ext.rating
    price_per_person REAL,               -- biz_ext.cost
    city TEXT DEFAULT '西安',
    district TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pois_amap_id ON pois(amap_id);
CREATE INDEX IF NOT EXISTS idx_pois_city ON pois(city);
CREATE INDEX IF NOT EXISTS idx_pois_category ON pois(category);
CREATE INDEX IF NOT EXISTS idx_pois_latlng ON pois(lat, lng);
CREATE INDEX IF NOT EXISTS idx_pois_name ON pois(name);
CREATE INDEX IF NOT EXISTS idx_pois_rating ON pois(rating);

-- 品类映射表：高德 subcategory → 大类 category
CREATE TABLE IF NOT EXISTS category_map (
    subcategory TEXT PRIMARY KEY,
    category TEXT NOT NULL
);
