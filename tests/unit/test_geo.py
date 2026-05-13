"""Unit tests for app.algorithms.geo — haversine distance and projection."""

import pytest

from app.algorithms.geo import haversine, project_ratio


class TestHaversine:
    """Tests for haversine distance calculation."""

    def test_same_point_returns_zero(self):
        """haversine(same_point, same_point) should be 0."""
        assert haversine(39.9042, 116.4074, 39.9042, 116.4074) == 0

    def test_return_type_is_int(self):
        """haversine should always return an int."""
        result = haversine(40.0, 116.0, 39.0, 117.0)
        assert isinstance(result, int)

    def test_symmetry(self):
        """haversine(a, b) == haversine(b, a)."""
        a = haversine(34.256, 108.943, 31.230, 121.474)
        b = haversine(31.230, 121.474, 34.256, 108.943)
        assert a == b

    def test_known_latitude_distance(self):
        """1 degree of latitude ≈ 111,195 m (along the same meridian)."""
        d = haversine(0.0, 0.0, 1.0, 0.0)
        # Expected ~111,195 m; allow ±200 m for rounding
        assert 110_000 < d < 112_000

    def test_multiple_symmetries(self):
        """Multiple random pairs should be symmetric."""
        pairs = [
            ((34.0, 108.0), (40.0, 116.0)),
            ((-33.0, 151.0), (48.0, 2.0)),
            ((0.0, 0.0), (-23.0, -46.0)),
        ]
        for (lat1, lng1), (lat2, lng2) in pairs:
            assert haversine(lat1, lng1, lat2, lng2) == haversine(lat2, lng2, lat1, lng1)


class TestProjectRatio:
    """Tests for project_ratio — project point onto origin→dest line."""

    def test_at_origin(self):
        """Point at origin should return ~0.0."""
        origin = {"lat": 34.0, "lng": 108.0}
        dest = {"lat": 40.0, "lng": 116.0}
        result = project_ratio(34.0, 108.0, origin, dest)
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_at_destination(self):
        """Point at destination should return ~1.0."""
        origin = {"lat": 34.0, "lng": 108.0}
        dest = {"lat": 40.0, "lng": 116.0}
        result = project_ratio(40.0, 116.0, origin, dest)
        assert result == pytest.approx(1.0, abs=1e-6)

    def test_midpoint(self):
        """Point at midpoint should return ~0.5."""
        origin = {"lat": 0.0, "lng": 0.0}
        dest = {"lat": 10.0, "lng": 10.0}
        result = project_ratio(5.0, 5.0, origin, dest)
        assert result == pytest.approx(0.5, abs=1e-6)

    def test_midpoint_along_lat_only(self):
        """Midpoint when moving north."""
        origin = {"lat": 30.0, "lng": 120.0}
        dest = {"lat": 40.0, "lng": 120.0}
        result = project_ratio(35.0, 120.0, origin, dest)
        assert result == pytest.approx(0.5, abs=1e-6)

    def test_beyond_line_clamped_to_zero(self):
        """Point before the origin should be clamped to 0.0."""
        origin = {"lat": 30.0, "lng": 120.0}
        dest = {"lat": 40.0, "lng": 120.0}
        result = project_ratio(20.0, 120.0, origin, dest)
        assert result == 0.0

    def test_beyond_line_clamped_to_one(self):
        """Point past the destination should be clamped to 1.0."""
        origin = {"lat": 30.0, "lng": 120.0}
        dest = {"lat": 40.0, "lng": 120.0}
        result = project_ratio(50.0, 120.0, origin, dest)
        assert result == 1.0

    def test_zero_length_segment(self):
        """When origin == dest, project_ratio should return 0.5."""
        origin = {"lat": 30.0, "lng": 120.0}
        dest = {"lat": 30.0, "lng": 120.0}
        result = project_ratio(35.0, 121.0, origin, dest)
        assert result == 0.5

    def test_beyond_line_both_sides(self):
        """Points far before/after line should be clamped properly."""
        origin = {"lat": 0.0, "lng": 0.0}
        dest = {"lat": 0.0, "lng": 10.0}
        # Before origin
        assert project_ratio(0.0, -5.0, origin, dest) == 0.0
        # After destination
        assert project_ratio(0.0, 15.0, origin, dest) == 1.0
