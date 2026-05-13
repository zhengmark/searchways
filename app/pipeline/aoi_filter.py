"""AOI (Area of Interest) spatial filter for corridor POIs.

When the user says "XX附近" without a destination, corridor POIs can span
the entire projected route, including far-away areas. This module detects
local/area queries and filters POIs to within a radius of the focal point.
"""

from app.algorithms.geo import haversine

# Keywords that trigger local-area mode
_LOCAL_KEYWORDS = {"附近", "周边", "附近有", "这附近", "这边", "这一带", "附近哪儿", "就近", "就近找"}

# Default radius for "nearby" queries (meters)
DEFAULT_RADIUS_M = 3000


def is_local_query(user_input: str) -> bool:
    """Check if this is a local/area query (no destination, just 'nearby')."""
    return any(kw in user_input for kw in _LOCAL_KEYWORDS)


def filter_by_radius(pois: list, center_lat: float, center_lng: float, radius_m: float = None) -> list:
    """Filter POIs to within radius_m of center point.

    Returns POIs sorted by distance (closest first), each with _aoi_dist_m added.
    """
    if radius_m is None:
        radius_m = DEFAULT_RADIUS_M

    filtered = []
    for p in pois:
        d = haversine(center_lat, center_lng, p.get("lat", 0), p.get("lng", 0))
        if d <= radius_m:
            p["_aoi_dist_m"] = int(d)
            filtered.append(p)

    filtered.sort(key=lambda p: p["_aoi_dist_m"])
    return filtered


def filter_adaptive(
    pois: list,
    origin_lat: float,
    origin_lng: float,
    dest_lat: float = None,
    dest_lng: float = None,
    user_input: str = "",
) -> list:
    """Smart filter: use AOI radius if local query, otherwise keep all.

    - Local query (no dest): filter by radius around origin
    - Has destination: keep all corridor pois (frontend handles projection-based display)
    """
    if dest_lat is None or is_local_query(user_input):
        return filter_by_radius(pois, origin_lat, origin_lng)
    return pois
