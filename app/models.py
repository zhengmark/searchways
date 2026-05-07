from pydantic import BaseModel
from typing import Optional


class POI(BaseModel):
    name: str
    address: str
    category: str
    lat: float
    lng: float
    rating: Optional[float] = None
    price_per_person: Optional[int] = None
    review_summary: Optional[str] = None


class RouteStop(BaseModel):
    order: int
    poi: POI
    arrival_time: Optional[str] = None
    duration_minutes: Optional[int] = None
    notes: str = ""


class Route(BaseModel):
    title: str
    summary: str
    stops: list[RouteStop]
    total_duration_minutes: int
    total_distance_km: float


class UserIntent(BaseModel):
    origin: str
    destination: Optional[str] = None
    date: Optional[str] = None
    time_budget_hours: Optional[float] = None
    group_type: Optional[str] = None  # solo / couple / family / friends
    preferences: list[str] = []
    budget_per_person: Optional[int] = None
    raw_input: str
