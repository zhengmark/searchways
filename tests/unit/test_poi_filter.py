"""Unit tests for app.algorithms.poi_filter — POI filtering utilities."""

from app.algorithms.poi_filter import (
    deduplicate_by_name,
    filter_by_category,
    filter_by_coords,
    filter_near_anchor,
    normalize_keywords,
)


class TestFilterByCategory:
    """Tests for filter_by_category."""

    def test_blacklisted_category_filtered(self):
        """POIs with blacklisted categories should be removed."""
        pois = [
            {"name": "打印店", "category": "打印"},
            {"name": "复印中心", "category": "复印;服务"},
            {"name": "维修铺", "category": "维修", "type": "家电维修"},
        ]
        result = filter_by_category(pois)
        assert len(result) == 0

    def test_non_blacklisted_category_stays(self):
        """POIs not matching blacklist should remain."""
        pois = [
            {"name": "兰州拉面", "category": "美食;中餐"},
            {"name": "星巴克", "category": "咖啡;茶饮"},
            {"name": "海底捞", "category": "火锅"},
        ]
        result = filter_by_category(pois)
        assert len(result) == 3
        assert result[0]["name"] == "兰州拉面"

    def test_blacklist_in_type_field(self):
        """Blacklist match in 'type' field should also filter."""
        pois = [
            {"name": "某店", "category": "生活服务", "type": "图文快印"},
        ]
        result = filter_by_category(pois)
        assert len(result) == 0

    def test_empty_list(self):
        """Empty input should return empty list."""
        assert filter_by_category([]) == []

    def test_mixed_pois(self):
        """Mixed POIs: some filtered, some kept."""
        pois = [
            {"name": "打印店", "category": "打印"},
            {"name": "美食城", "category": "美食"},
            {"name": "洗车行", "category": "洗车"},
            {"name": "公园", "category": "景点;公园"},
        ]
        result = filter_by_category(pois)
        assert len(result) == 2
        names = [p["name"] for p in result]
        assert "美食城" in names
        assert "公园" in names

    def test_blacklist_substring_match(self):
        """Blacklist '广告' should filter '广告制作'."""
        pois = [
            {"name": "广告公司", "category": "广告制作"},
        ]
        result = filter_by_category(pois)
        assert len(result) == 0

    def test_partial_match_does_not_filter_innocent(self):
        """'中介' should not match '中外合资餐厅' unless it appears as substring."""
        # "中介" is not a substring of "中外合资" - "中"+"介"... wait.
        # Actually "中外合资" contains "中介"? Let's see: 中-外-合-资. No, it does not contain "中介".
        pois = [
            {"name": "中外合资餐厅", "category": "美食"},
        ]
        result = filter_by_category(pois)
        assert len(result) == 1


class TestFilterByCoords:
    """Tests for filter_by_coords."""

    def test_valid_coords_stay(self):
        """POI with valid lat/lng should remain."""
        pois = [
            {"name": "Valid", "lat": 34.256, "lng": 108.943},
        ]
        result = filter_by_coords(pois)
        assert len(result) == 1

    def test_missing_lat_filtered(self):
        """POI without 'lat' key should be filtered."""
        pois = [
            {"name": "NoLat", "lng": 108.943},
        ]
        result = filter_by_coords(pois)
        assert len(result) == 0

    def test_missing_lng_filtered(self):
        """POI without 'lng' key should be filtered."""
        pois = [
            {"name": "NoLng", "lat": 34.256},
        ]
        result = filter_by_coords(pois)
        assert len(result) == 0

    def test_lat_is_none_filtered(self):
        """POI with lat=None should be filtered via 'is not None' check."""
        pois = [
            {"name": "NoneLat", "lat": None, "lng": 108.943},
        ]
        result = filter_by_coords(pois)
        assert len(result) == 0

    def test_lng_is_none_filtered(self):
        """POI with lng=None should be filtered."""
        pois = [
            {"name": "NoneLng", "lat": 34.256, "lng": None},
        ]
        result = filter_by_coords(pois)
        assert len(result) == 0

    def test_zero_lat_is_valid(self):
        """lat=0 is valid (not None), should stay."""
        pois = [
            {"name": "ZeroLat", "lat": 0.0, "lng": 108.943},
        ]
        result = filter_by_coords(pois)
        assert len(result) == 1

    def test_empty_list(self):
        """Empty input should return empty list."""
        assert filter_by_coords([]) == []

    def test_mixed_pois(self):
        """Only POIs with both lat and lng survive."""
        pois = [
            {"name": "Good", "lat": 34.0, "lng": 108.0},
            {"name": "Bad1", "lng": 108.0},
            {"name": "Bad2", "lat": None, "lng": 108.0},
            {"name": "Good2", "lat": 40.0, "lng": 116.0},
        ]
        result = filter_by_coords(pois)
        assert len(result) == 2
        assert result[0]["name"] == "Good"
        assert result[1]["name"] == "Good2"


class TestFilterNearAnchor:
    """Tests for filter_near_anchor."""

    def test_poi_near_anchor_filtered(self):
        """POI within min_distance of anchor gets filtered."""
        anchor = (34.256000, 108.943000)  # ~0m from POI
        pois = [
            {"name": "Near", "lat": 34.256000, "lng": 108.943000},
            {"name": "Far", "lat": 34.300000, "lng": 108.943000},
        ]
        result = filter_near_anchor(pois, anchor, anchor_name="起点", min_distance=100)
        # Near should be filtered (~0m distance), Far stays
        assert len(result) == 1
        assert result[0]["name"] == "Far"

    def test_poi_far_from_anchor_stays(self):
        """POI far from anchor stays."""
        anchor = (30.0, 120.0)
        pois = [
            {"name": "Far", "lat": 40.0, "lng": 116.0},
        ]
        result = filter_near_anchor(pois, anchor, anchor_name="起点", min_distance=100)
        assert len(result) == 1

    def test_empty_anchor_coords_returns_all(self):
        """When anchor_coords is empty (falsy), return all POIs."""
        pois = [
            {"name": "P1", "lat": 34.256, "lng": 108.943},
            {"name": "P2", "lat": 34.300, "lng": 108.900},
        ]
        result = filter_near_anchor(pois, (), min_distance=100)
        assert len(result) == 2

    def test_none_anchor_coords_returns_all(self):
        """When anchor_coords is None, return all POIs."""
        pois = [
            {"name": "P1", "lat": 34.256, "lng": 108.943},
        ]
        result = filter_near_anchor(pois, None, min_distance=100)
        assert len(result) == 1

    def test_anchor_name_match_filtered(self):
        """POI with same name as anchor gets filtered."""
        anchor = (34.256, 108.943)
        pois = [
            {"name": "AnchorPlace", "lat": 40.0, "lng": 116.0},  # far but same name
        ]
        result = filter_near_anchor(pois, anchor, anchor_name="AnchorPlace", min_distance=100)
        assert len(result) == 0

    def test_custom_min_distance(self):
        """Custom min_distance is respected."""
        anchor = (34.256, 108.943)
        pois = [
            {"name": "Close", "lat": 34.257, "lng": 108.943},  # ~111m away
        ]
        # With min_distance=50, stays
        result = filter_near_anchor(pois, anchor, anchor_name="起点", min_distance=50)
        assert len(result) == 1
        # With min_distance=200, filtered
        result = filter_near_anchor(pois, anchor, anchor_name="起点", min_distance=200)
        assert len(result) == 0


class TestDeduplicateByName:
    """Tests for deduplicate_by_name."""

    def test_duplicate_names_only_first_kept(self):
        """First occurrence of each name survives."""
        pois = [
            {"name": "A", "lat": 1.0, "lng": 1.0},
            {"name": "B", "lat": 2.0, "lng": 2.0},
            {"name": "A", "lat": 3.0, "lng": 3.0},
            {"name": "B", "lat": 4.0, "lng": 4.0},
        ]
        result = deduplicate_by_name(pois)
        assert len(result) == 2
        assert result[0]["name"] == "A"
        assert result[0]["lat"] == 1.0  # first occurrence kept
        assert result[1]["name"] == "B"
        assert result[1]["lat"] == 2.0  # first occurrence kept

    def test_all_unique_all_kept(self):
        """When all names are unique, all POIs are kept."""
        pois = [
            {"name": "A", "lat": 1.0, "lng": 1.0},
            {"name": "B", "lat": 2.0, "lng": 2.0},
            {"name": "C", "lat": 3.0, "lng": 3.0},
        ]
        result = deduplicate_by_name(pois)
        assert len(result) == 3

    def test_empty_returns_empty(self):
        """Empty list returns empty list."""
        assert deduplicate_by_name([]) == []

    def test_empty_name_should_skip(self):
        """POIs with empty name should be skipped."""
        pois = [
            {"name": "", "lat": 1.0, "lng": 1.0},
            {"name": "Valid", "lat": 2.0, "lng": 2.0},
            {"name": "", "lat": 3.0, "lng": 3.0},
        ]
        result = deduplicate_by_name(pois)
        # Only "Valid" should survive; empty names are skipped
        assert len(result) == 1
        assert result[0]["name"] == "Valid"

    def test_multiple_duplicates_with_different_counts(self):
        """A: 3 copies, B: 2 copies, C: 1 copy → A, B, C total 3."""
        pois = [
            {"name": "A", "lat": 1.0, "lng": 1.0},
            {"name": "A", "lat": 1.1, "lng": 1.1},
            {"name": "A", "lat": 1.2, "lng": 1.2},
            {"name": "B", "lat": 2.0, "lng": 2.0},
            {"name": "B", "lat": 2.1, "lng": 2.1},
            {"name": "C", "lat": 3.0, "lng": 3.0},
        ]
        result = deduplicate_by_name(pois)
        assert len(result) == 3
        names = [p["name"] for p in result]
        assert names == ["A", "B", "C"]


class TestNormalizeKeywords:
    """Tests for normalize_keywords."""

    def test_known_keyword_maps_to_expanded(self):
        """'吃' should map to ['美食']."""
        result = normalize_keywords(["吃"])
        assert result == ["美食"]

    def test_unknown_keyword_stays(self):
        """Unknown keyword should remain as-is."""
        result = normalize_keywords(["烧烤"])
        assert result == ["烧烤"]

    def test_dedup_across_expansions(self):
        """Duplicate keywords from expansions should be removed."""
        result = normalize_keywords(["吃", "美食"])
        assert result == ["美食"]

    def test_multiple_keywords(self):
        """Multiple known and unknown keywords."""
        result = normalize_keywords(["吃", "拍照", "火锅"])
        # "吃" → "美食", "拍照" → "景点,网红" → ["景点", "网红"]
        assert "美食" in result
        assert "景点" in result
        assert "网红" in result
        assert "火锅" in result
        # Should be deduped and order preserved
        assert len(result) == 4

    def test_compound_keyword_match(self):
        """'宵夜' → '小吃,烧烤,火锅'."""
        result = normalize_keywords(["宵夜"])
        assert result == ["小吃", "烧烤", "火锅"]

    def test_empty_list(self):
        """Empty input should return empty list."""
        assert normalize_keywords([]) == []

    def test_dedup_preserves_order(self):
        """Deduplication preserves first-seen order."""
        result = normalize_keywords(["玩", "喝", "逛"])
        # "玩" → "景点,公园", "喝" → "咖啡,茶饮", "逛" → "商场,购物"
        assert result == ["景点", "公园", "咖啡", "茶饮", "商场", "购物"]
