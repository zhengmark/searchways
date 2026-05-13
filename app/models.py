from pydantic import BaseModel


class POI(BaseModel):
    name: str
    address: str
    category: str
    lat: float
    lng: float
    rating: float | None = None
    price_per_person: int | None = None
    review_summary: str | None = None


class RouteStop(BaseModel):
    order: int
    poi: POI
    arrival_time: str | None = None
    duration_minutes: int | None = None
    notes: str = ""


class Route(BaseModel):
    title: str
    summary: str
    stops: list[RouteStop]
    total_duration_minutes: int
    total_distance_km: float


class UserIntent(BaseModel):
    origin: str
    destination: str | None = None
    date: str | None = None
    time_budget_hours: float | None = None
    group_type: str | None = None  # solo / couple / family / friends
    preferences: list[str] = []
    budget_per_person: int | None = None
    raw_input: str


# ── Phase 2-4: 交互式路线编辑模型 ──────────────────


class CorridorPoi(BaseModel):
    """走廊候选 POI"""

    id: str
    name: str
    lat: float
    lng: float
    category: str = ""
    rating: float | None = None
    price_per_person: float | None = None
    address: str = ""
    cluster_id: int = 0
    projection_ratio: float = 0.0
    perpendicular_km: float = 0.0
    recommendation_reasons: dict = {}
    selected: bool = False


class SelectPoiRequest(BaseModel):
    poi_id: str


class ConnectPoiRequest(BaseModel):
    from_poi_id: str
    to_poi_id: str
    mode: str = "auto"  # walk | bike | transit | drive | auto


class ReorderRequest(BaseModel):
    poi_ids: list[str]


class TransitQueryRequest(BaseModel):
    from_lat: float
    from_lng: float
    to_lat: float
    to_lng: float
