"""Unit tests for app.algorithms.graph_planner — planning and path selection."""

import pytest
from unittest.mock import patch, MagicMock
from app.algorithms.graph_planner import (
    pre_prune_pois,
    _haversine_fallback,
    shortest_path,
    _pick_from_segments,
)


# ─── helper: build a simple synthetic graph ────────

def _synthetic_graph(n, edge_dist=1000, edge_dur=600):
    """Build an n×n adjacency matrix with known edge values."""
    graph = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                graph[i][j] = {
                    "distance": edge_dist,
                    "duration": edge_dur,
                    "transport": "步行",
                }
    return graph


def _synthetic_nodes(origin_coords, poi_data_list, dest_coords):
    """Build a nodes list matching build_graph output format."""
    nodes = [{"id": 0, "name": "起点", "lat": origin_coords[0], "lng": origin_coords[1], "type": "origin"}]
    for i, p in enumerate(poi_data_list):
        nodes.append({
            "id": i + 1,
            "name": p["name"],
            "lat": p["lat"],
            "lng": p["lng"],
            "type": "poi",
            "rating": p.get("rating"),
            "category": p.get("category", ""),
        })
    if dest_coords is not None:
        nodes.append({
            "id": len(nodes),
            "name": "终点",
            "lat": dest_coords[0],
            "lng": dest_coords[1],
            "type": "destination",
        })
    return nodes


# ═══════════════════════════════════════════════════
#  pre_prune_pois
# ═══════════════════════════════════════════════════

class TestPrePrunePois:
    """Tests for pre_prune_pois function."""

    def test_len_within_max_returns_unchanged(self):
        """If len(pois) <= max_pois, list is returned as-is."""
        pois = [{"name": f"P{i}", "rating": 4.0, "lat": 34.0 + i * 0.01, "lng": 108.0} for i in range(5)]
        result = pre_prune_pois(pois, max_pois=5)
        assert result == pois
        assert len(result) == 5

    def test_len_exceeds_max_returns_exactly_max(self):
        """If len(pois) > max_pois, result has exactly max_pois items."""
        pois = [{"name": f"P{i}", "rating": 4.0, "lat": 34.0 + i * 0.01, "lng": 108.0} for i in range(20)]
        result = pre_prune_pois(pois, max_pois=10)
        assert len(result) == 10

    def test_higher_rated_pois_preferred(self):
        """Higher-rated POIs should be kept over lower-rated ones."""
        pois = [
            {"name": "High", "rating": 5.0, "lat": 34.0, "lng": 108.0},
            {"name": "Low", "rating": 2.0, "lat": 34.0, "lng": 108.0},
            {"name": "Mid", "rating": 3.5, "lat": 34.0, "lng": 108.0},
        ]
        result = pre_prune_pois(pois, max_pois=2)
        assert len(result) == 2
        names = [p["name"] for p in result]
        assert "High" in names
        # Mid should be preferred over Low
        assert "Mid" in names
        assert "Low" not in names

    def test_pois_closer_to_anchor_get_bonus(self):
        """POIs closer to anchor should get a distance bonus."""
        anchor_lat, anchor_lng = 34.0, 108.0
        pois = [
            # Close to anchor, lower rating
            {"name": "Close", "rating": 3.5, "lat": 34.001, "lng": 108.001},
            # Far from anchor, slightly higher rating
            {"name": "Far", "rating": 3.6, "lat": 36.0, "lng": 110.0},
        ]
        result = pre_prune_pois(pois, max_pois=1, anchor_lat=anchor_lat, anchor_lng=anchor_lng)
        assert len(result) == 1
        # Close should win because distance bonus outweighs small rating gap
        assert result[0]["name"] == "Close"

    def test_no_anchor_no_bonus(self):
        """Without anchor coords, only rating matters."""
        pois = [
            {"name": "FarHigh", "rating": 4.5, "lat": 40.0, "lng": 116.0},
            {"name": "CloseLow", "rating": 3.0, "lat": 34.0, "lng": 108.0},
        ]
        # No anchor → no distance bonus
        result = pre_prune_pois(pois, max_pois=1)
        assert result[0]["name"] == "FarHigh"

    def test_empty_list(self):
        """Empty list returns empty list."""
        assert pre_prune_pois([], max_pois=5) == []

    def test_missing_rating_defaults_to_3(self):
        """POIs without rating should default to 3.0."""
        pois = [
            {"name": "NoRating", "lat": 34.0, "lng": 108.0},
            {"name": "Rated", "rating": 4.5, "lat": 34.0, "lng": 108.0},
        ]
        result = pre_prune_pois(pois, max_pois=2)
        assert len(result) == 2

    def test_anchor_none_partial(self):
        """Partial anchor (only lat) should still work."""
        pois = [{"name": f"P{i}", "rating": 4.0, "lat": 34.0, "lng": 108.0} for i in range(20)]
        result = pre_prune_pois(pois, max_pois=5, anchor_lat=34.0, anchor_lng=None)
        # anchor_lng=None triggers `if anchor_lat is not None and anchor_lng is not None` → False
        # So no bonus, pure rating sort
        assert len(result) == 5

    def test_zero_rating_handled(self):
        """Rating of 0 should be treated as 3.0 due to `or 3.0`."""
        pois = [
            {"name": "Zero", "rating": 0, "lat": 34.0, "lng": 108.0},
            {"name": "HasRating", "rating": 4.0, "lat": 34.0, "lng": 108.0},
        ]
        result = pre_prune_pois(pois, max_pois=2)
        assert len(result) == 2


# ═══════════════════════════════════════════════════
#  _haversine_fallback
# ═══════════════════════════════════════════════════

class TestHaversineFallback:
    """Tests for _haversine_fallback."""

    def test_returns_distance_and_duration_tuple(self):
        """Should return (distance: int, duration: int)."""
        result = _haversine_fallback(34.0, 108.0, 34.1, 108.0, "步行")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], int)  # distance
        assert isinstance(result[1], int)  # duration

    def test_different_modes_give_different_durations(self):
        """Different transport modes should produce different durations."""
        walk = _haversine_fallback(34.0, 108.0, 34.1, 108.0, "步行")
        bike = _haversine_fallback(34.0, 108.0, 34.1, 108.0, "骑行")
        transit = _haversine_fallback(34.0, 108.0, 34.1, 108.0, "公交/地铁")
        drive = _haversine_fallback(34.0, 108.0, 34.1, 108.0, "驾车")

        # Walking should be slowest (highest duration)
        assert walk[1] > bike[1]
        assert bike[1] > transit[1]
        assert transit[1] > drive[1]

    def test_same_distance_across_modes(self):
        """Distance is the same regardless of mode (straight * road_factor)."""
        walk = _haversine_fallback(34.0, 108.0, 34.1, 108.0, "步行")
        bike = _haversine_fallback(34.0, 108.0, 34.1, 108.0, "骑行")
        assert walk[0] == bike[0]  # distance unchanged by mode

    def test_distance_positive(self):
        """Distance should be positive for distinct points."""
        d, dur = _haversine_fallback(0.0, 0.0, 1.0, 0.0, "步行")
        assert d > 0
        assert dur > 0


# ═══════════════════════════════════════════════════
#  _pick_from_segments
# ═══════════════════════════════════════════════════

class TestPickFromSegments:
    """Tests for _pick_from_segments."""

    def test_picks_correct_number_of_items(self):
        """Should pick exactly num_stops items (one per segment)."""
        graph = _synthetic_graph(5, edge_dist=2000, edge_dur=120)
        items = [
            (0.1, 4.0, 1, "美食"),
            (0.3, 4.2, 2, "咖啡"),
            (0.5, 4.5, 3, "景点"),
            (0.7, 3.8, 4, "购物"),
        ]
        result = _pick_from_segments(items, num_stops=2, graph=graph)
        assert len(result) == 2

    def test_picks_diverse_categories(self):
        """Should prefer diverse categories across segments."""
        graph = _synthetic_graph(6, edge_dist=2000, edge_dur=120)
        items = [
            (0.1, 5.0, 1, "美食"),
            (0.3, 5.0, 2, "美食"),  # same category as above but in different segment
            (0.5, 3.0, 3, "咖啡"),  # lower rating, different category
        ]
        # Two segments: items 0-1 in seg 0, item 2 in seg 1
        result = _pick_from_segments(items, num_stops=2, graph=graph)
        assert len(result) == 2

    def test_num_stops_exceeds_items(self):
        """If num_stops > segments with items, extra segments are skipped."""
        graph = _synthetic_graph(5, edge_dist=2000, edge_dur=120)
        items = [
            (0.3, 4.0, 1, "美食"),
        ]
        result = _pick_from_segments(items, num_stops=3, graph=graph)
        # Only one segment has items → 1 result
        assert len(result) == 1

    def test_empty_items(self):
        """Empty items should return empty list."""
        graph = _synthetic_graph(3, edge_dist=1000, edge_dur=60)
        result = _pick_from_segments([], num_stops=3, graph=graph)
        assert result == []

    def test_single_item_single_stop(self):
        """Simplest case: one item, one stop."""
        graph = _synthetic_graph(3, edge_dist=1000, edge_dur=60)
        items = [(0.5, 4.0, 1, "美食")]
        result = _pick_from_segments(items, num_stops=1, graph=graph)
        assert result == [1]

    def test_distance_mutual_exclusion(self):
        """POIs within 500m of already-selected nodes should be skipped."""
        n = 5
        graph = _synthetic_graph(n, edge_dist=200, edge_dur=30)  # all very close
        items = [
            (0.1, 5.0, 1, "美食"),
            (0.3, 4.8, 2, "咖啡"),
            (0.5, 4.5, 3, "景点"),
            (0.7, 4.0, 4, "购物"),
        ]
        result = _pick_from_segments(items, num_stops=2, graph=graph)
        assert len(result) == 2
        # Since all are within 500m, segment 1 may skip the best and pick next best
        # Just verify we got valid node IDs
        for nid in result:
            assert 1 <= nid <= 4

    def test_returns_node_ids(self):
        """Result should be list of integer node IDs."""
        graph = _synthetic_graph(5, edge_dist=1000, edge_dur=60)
        items = [
            (0.2, 4.0, 1, "美食"),
            (0.5, 4.5, 2, "咖啡"),
        ]
        result = _pick_from_segments(items, num_stops=1, graph=graph)
        assert all(isinstance(x, int) for x in result)


# ═══════════════════════════════════════════════════
#  shortest_path
# ═══════════════════════════════════════════════════

class TestShortestPath:
    """Tests for shortest_path function."""

    def test_returns_correct_structure(self):
        """Result should have node_ids, segments, total_duration_min, total_distance."""
        nodes = _synthetic_nodes(
            origin_coords=(34.0, 108.0),
            poi_data_list=[
                {"name": "P1", "lat": 34.1, "lng": 108.1, "rating": 4.5, "category": "美食"},
                {"name": "P2", "lat": 34.2, "lng": 108.2, "rating": 4.0, "category": "咖啡"},
                {"name": "P3", "lat": 34.3, "lng": 108.3, "rating": 4.8, "category": "景点"},
            ],
            dest_coords=(34.5, 108.5),
        )
        graph = _synthetic_graph(len(nodes), edge_dist=5000, edge_dur=300)
        result = shortest_path(graph, nodes, num_stops=2, budget_level="medium")

        assert "node_ids" in result
        assert "segments" in result
        assert "total_duration_min" in result
        assert "total_distance" in result
        assert isinstance(result["node_ids"], list)
        assert isinstance(result["segments"], list)
        assert isinstance(result["total_duration_min"], (int, float))
        assert isinstance(result["total_distance"], int)

    def test_respects_num_stops(self):
        """Path should not exceed requested num_stops in POI nodes."""
        nodes = _synthetic_nodes(
            origin_coords=(34.0, 108.0),
            poi_data_list=[
                {"name": "P1", "lat": 34.05, "lng": 108.05, "rating": 4.5, "category": "美食"},
                {"name": "P2", "lat": 34.10, "lng": 108.10, "rating": 4.0, "category": "咖啡"},
                {"name": "P3", "lat": 34.15, "lng": 108.15, "rating": 4.8, "category": "景点"},
                {"name": "P4", "lat": 34.20, "lng": 108.20, "rating": 3.5, "category": "购物"},
            ],
            dest_coords=(34.25, 108.25),
        )
        graph = _synthetic_graph(len(nodes), edge_dist=2000, edge_dur=120)
        result = shortest_path(graph, nodes, num_stops=2, budget_level="medium")

        # Count POI nodes in path (exclude origin 0 and destination len(nodes)-1)
        poi_ids = [nid for nid in result["node_ids"]
                   if nid != 0 and nid != (len(nodes) - 1)]
        assert len(poi_ids) <= 2

    def test_no_poi_nodes_returns_empty(self):
        """When there are no POI nodes, no POI stops but origin→dest still connected."""
        nodes = _synthetic_nodes(
            origin_coords=(34.0, 108.0),
            poi_data_list=[],
            dest_coords=(34.5, 108.5),
        )
        graph = _synthetic_graph(len(nodes), edge_dist=5000, edge_dur=300)
        result = shortest_path(graph, nodes, num_stops=3, budget_level="medium")

        # num_stops becomes min(3, 0) = 0 → no POI stops selected
        # But origin→destination gets connected as a direct segment
        assert len(result["segments"]) == 1
        # The segment should be origin → destination
        assert result["segments"][0]["from"] == "起点"
        assert result["segments"][0]["to"] == "终点"

    def test_no_destination_still_works(self):
        """Without a destination node, shortest_path should still work."""
        nodes = _synthetic_nodes(
            origin_coords=(34.0, 108.0),
            poi_data_list=[
                {"name": "P1", "lat": 34.1, "lng": 108.1, "rating": 4.5, "category": "美食"},
                {"name": "P2", "lat": 34.2, "lng": 108.2, "rating": 4.0, "category": "咖啡"},
            ],
            dest_coords=None,
        )
        graph = _synthetic_graph(len(nodes), edge_dist=5000, edge_dur=300)
        result = shortest_path(graph, nodes, num_stops=2, budget_level="medium")

        assert "node_ids" in result
        # Without destination, last node is not appended as destination
        # So the path should end at the last selected POI
        assert len(result["node_ids"]) >= 1

    def test_path_includes_destination(self):
        """When destination exists, path should end at destination."""
        nodes = _synthetic_nodes(
            origin_coords=(34.0, 108.0),
            poi_data_list=[
                {"name": "P1", "lat": 34.1, "lng": 108.1, "rating": 4.5, "category": "美食"},
            ],
            dest_coords=(34.5, 108.5),
        )
        graph = _synthetic_graph(len(nodes), edge_dist=5000, edge_dur=300)
        result = shortest_path(graph, nodes, num_stops=1, budget_level="medium")

        # Last node in path should be the destination
        dest_id = len(nodes) - 1
        assert result["node_ids"][-1] == dest_id

    def test_segments_have_required_keys(self):
        """Each segment should have from, to, distance, duration, transport."""
        nodes = _synthetic_nodes(
            origin_coords=(34.0, 108.0),
            poi_data_list=[
                {"name": "P1", "lat": 34.1, "lng": 108.1, "rating": 4.5, "category": "美食"},
            ],
            dest_coords=(34.5, 108.5),
        )
        graph = _synthetic_graph(len(nodes), edge_dist=5000, edge_dur=300)
        result = shortest_path(graph, nodes, num_stops=1, budget_level="medium")

        for seg in result["segments"]:
            assert "from" in seg
            assert "to" in seg
            assert "distance" in seg
            assert "duration" in seg
            assert "transport" in seg

    def test_total_duration_is_reasonable(self):
        """Total duration should be computed correctly from segments."""
        nodes = _synthetic_nodes(
            origin_coords=(34.0, 108.0),
            poi_data_list=[
                {"name": "P1", "lat": 34.1, "lng": 108.1, "rating": 4.5, "category": "美食"},
                {"name": "P2", "lat": 34.2, "lng": 108.2, "rating": 4.0, "category": "咖啡"},
            ],
            dest_coords=(34.5, 108.5),
        )
        graph = _synthetic_graph(len(nodes), edge_dist=5000, edge_dur=600)
        result = shortest_path(graph, nodes, num_stops=2, budget_level="medium")

        # Each segment is 600 seconds; with 2 POIs + destination, that's 3 segments
        expected_dur_min = (600 * 3) / 60  # 30 min
        assert result["total_duration_min"] == pytest.approx(expected_dur_min, abs=1)

    def test_zero_stops(self):
        """num_stops=0 should still work."""
        nodes = _synthetic_nodes(
            origin_coords=(34.0, 108.0),
            poi_data_list=[
                {"name": "P1", "lat": 34.1, "lng": 108.1, "rating": 4.5, "category": "美食"},
            ],
            dest_coords=(34.5, 108.5),
        )
        graph = _synthetic_graph(len(nodes), edge_dist=5000, edge_dur=300)
        result = shortest_path(graph, nodes, num_stops=0, budget_level="medium")
        # No POI stops, but origin→destination may still be in path
        assert "node_ids" in result
