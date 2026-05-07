"""POI 数据源统一入口 — 通过 USE_POI_DB 灰度切换 amap / SQLite."""
from app.config import USE_POI_DB

if USE_POI_DB:
    from db.repository import POIRepository
    _provider = POIRepository()
    search_poi = _provider.search_poi
    search_around = _provider.search_around
    search_along_route = _provider.search_along_route
else:
    from app.providers.amap_provider import search_poi, search_around, search_along_route  # noqa: F401
