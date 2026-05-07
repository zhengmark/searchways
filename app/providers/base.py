"""数据源抽象接口."""
from abc import ABC, abstractmethod


class POIProvider(ABC):
    """POI 数据源抽象接口——高德 API 和本地 DB 均实现此接口."""

    @abstractmethod
    def search_poi(self, keywords: str, location: str, radius_km: float = 3, limit: int = 5) -> list:
        ...

    @abstractmethod
    def search_around(self, location: str, keywords: str, radius: int = 3000, limit: int = 10) -> list:
        ...

    @abstractmethod
    def search_along_route(self, origin: str, destination: str, keywords: str,
                           radius: int = 3000, limit: int = 20) -> list:
        ...

    @abstractmethod
    def geocode(self, address: str, city: str = "") -> dict:
        ...

    @abstractmethod
    def robust_geocode(self, name: str, city: str) -> tuple:
        ...
