"""Linear referencing tests, on synthetic patterns laid out in feet and on recorded fixtures."""

import json
from datetime import datetime
from math import cos, degrees, radians
from pathlib import Path

import pytest

from aaata_bus import Route, RouteData
from aaata_bus.geometry import (
    EARTH_RADIUS_FT,
    PatternGeometry,
    check_stop_distances,
    pattern_geometries,
    snap_sightings,
)
from aaata_bus.schema import (
    Bus,
    BusesForRouteResponse,
    PredictionsForBusResponse,
    RoutePattern,
    RoutePoint,
    RouteStop,
)

FIXTURES = Path(__file__).parent / "fixtures"

LAT0, LON0 = 42.28, -83.74


def latlon(x_ft: float, y_ft: float) -> tuple[float, float]:
    """(lat, lon) of a point x feet east and y feet north of (LAT0, LON0)."""
    lat = LAT0 + degrees(y_ft / EARTH_RADIUS_FT)
    lon = LON0 + degrees(x_ft / (EARTH_RADIUS_FT * cos(radians(LAT0))))
    return lat, lon


def point(x: float, y: float, stop: str | None = None, bypassed: bool = False) -> RoutePoint:
    lat, lon = latlon(x, y)
    return RoutePoint(
        latitude=lat,
        longitude=lon,
        is_bypassed=bypassed,
        stop=RouteStop(stop_id=stop, stop_name=f"Stop {stop}") if stop else None,
    )


def pattern(*points: RoutePoint, pattern_id: str = "1") -> RoutePattern:
    return RoutePattern(
        pattern_id=pattern_id, direction="TO BTC", direction_destination="TO BTC", points=list(points)
    )


def geometry(*points: RoutePoint) -> PatternGeometry:
    return PatternGeometry("22", pattern(*points))


def make_bus(x: float, y: float, pattern_id: str = "1", minutes: int = 0) -> Bus:
    lat, lon = latlon(x, y)
    return Bus(
        vehicle_id="561", consist=[], cars=[], rtpi_feed_name=[], mode="1",
        route_number="22", route_rtpi_feed_name=[], route_display="22",
        direction="East Bound", direction_destination="TO BTC", direction_code="E",
        latitude=lat, longitude=lon, pattern_id=pattern_id, pattern_destination="TO BTC",
        pattern_destination_rtpi_feed_name=[], run_number="1", destination_sign="To BTC",
        operator_id="1", dispatch_id="1", block_id="1", work_id_1="N/A", work_id_2="1",
        is_hidden=False, timestamp=datetime(2026, 10, 19, 12, minutes),
    )


def test_project_onto_straight_line():
    line = geometry(point(0, 0), point(1000, 0))
    assert line.length_ft == pytest.approx(1000, abs=0.01)

    projection = line.project(*latlon(300, 20))
    assert projection.dist_along_ft == pytest.approx(300, abs=0.01)
    assert projection.offset_ft == pytest.approx(20, abs=0.01)
    assert (projection.latitude, projection.longitude) == pytest.approx(latlon(300, 0))

    # Beyond the ends, points clamp to the end and the offset is the straight-line distance.
    past_end = line.project(*latlon(1030, 40))
    assert past_end.dist_along_ft == pytest.approx(1000, abs=0.01)
    assert past_end.offset_ft == pytest.approx(50, abs=0.01)


def test_point_at_inverts_project():
    bend = geometry(point(0, 0), point(600, 0), point(600, 800))
    for dist in (0, 250, 600, 1000, 1400):
        assert bend.project(*bend.point_at(dist)).dist_along_ft == pytest.approx(dist, abs=0.01)


def test_stops_are_located_along_the_path():
    line = geometry(point(0, 0, stop="A"), point(400, 0, stop="B"), point(1000, 0, stop="C"))
    assert [(s.stop_id, round(s.dist_along_ft)) for s in line.stops] == [("A", 0), ("B", 400), ("C", 1000)]
    assert line.stop_distances("B") == [pytest.approx(400)]
    assert line.stop_distances("missing") == []


def test_stop_spurs_are_removed():
    # TheRide draws a stop as a jog to the curb and back: A, STOP, A.
    line = geometry(point(0, 0), point(500, 0), point(500, 30, stop="S"), point(500, 0), point(1000, 0))
    assert line.length_ft == pytest.approx(1000, abs=0.01)
    assert line.stop_distances("S") == [pytest.approx(500, abs=0.01)]
    # A ping at the curb snaps to the street, not into the spur.
    assert line.project(*latlon(500, 25)).offset_ft == pytest.approx(25, abs=0.01)


def test_nested_stop_spurs_with_inexact_returns_are_removed():
    # A, B, STOP, B', A' where the return vertices are a foot off the outbound ones.
    line = geometry(
        point(0, 0), point(500, 0), point(510, 20), point(512, 40, stop="S"),
        point(510.5, 20.5), point(500.5, 0.5), point(1000, 0),
    )
    assert line.length_ft == pytest.approx(1000, abs=1)
    # The stop lands where it's level with the street, beside the spur's base.
    assert line.stop_distances("S") == [pytest.approx(512, abs=1)]


def test_long_out_and_back_to_a_stop_is_kept():
    # A real trip up a 300 ft side street to a stop and back is travel the bus does.
    line = geometry(point(0, 0), point(500, 0), point(500, 300, stop="S"), point(500, 0), point(1000, 0))
    assert line.length_ft == pytest.approx(1600, abs=0.01)
    assert line.stop_distances("S") == [pytest.approx(800, abs=0.01)]


def test_backtrack_without_a_stop_is_kept():
    line = geometry(point(0, 0), point(500, 0), point(500, 30), point(500, 0), point(1000, 0))
    assert line.length_ft == pytest.approx(1060, abs=0.01)


def test_near_hint_picks_the_pass_of_an_out_and_back_path():
    # Out along y=0 and back along y=40, as on a street driven both ways.
    loop = geometry(point(0, 0), point(1000, 0), point(1000, 40), point(0, 40))
    ping = latlon(200, 15)  # closer to the outbound leg

    assert loop.project(*ping).dist_along_ft == pytest.approx(200, abs=0.01)
    inbound = loop.project(*ping, near_ft=1800)
    assert inbound.dist_along_ft == pytest.approx(1840, abs=0.01)
    assert inbound.offset_ft == pytest.approx(25, abs=0.01)


def test_near_hint_does_not_drag_the_snap_along_the_same_pass():
    # Regression: the hint once picked whichever nearby segment was closest to
    # it, pulling snaps up to max_offset_ft back toward the previous ping.
    line = geometry(*(point(x, 0) for x in range(0, 1001, 50)))
    projection = line.project(*latlon(500, 5), near_ft=100)
    assert projection.dist_along_ft == pytest.approx(500, abs=0.01)
    assert projection.offset_ft == pytest.approx(5, abs=0.01)


def test_detoured_pattern_uses_the_detour_path():
    detoured = pattern(
        point(0, 0), point(500, 300), point(1000, 0),  # detour path buses take
        point(0, 0, bypassed=True), point(1000, 0, bypassed=True),  # regular path it bypasses
    )
    assert PatternGeometry("22", detoured).length_ft == pytest.approx(2 * 583.1, abs=0.1)
    assert PatternGeometry("22", detoured, use_bypassed=True).length_ft == pytest.approx(1000, abs=0.01)


def test_snap_sightings_tracks_a_bus_through_an_out_and_back_path():
    geometries = {"1": geometry(point(0, 0), point(1000, 0), point(1000, 40), point(0, 40))}
    # Heading back west on the y=40 leg, though every ping is nearer the y=0 leg...
    sightings = [make_bus(1000, 30, minutes=0), make_bus(600, 15, minutes=1), make_bus(200, 15, minutes=2)]
    snapped = snap_sightings(sightings, geometries)
    # ...the first ping is only near the turn, so each later one follows it back.
    assert [round(p.projection.dist_along_ft) for p in snapped if p.projection] == [1030, 1440, 1840]
    assert all(p.on_route for p in snapped)


def test_snap_sightings_flags_off_route_and_unknown_patterns():
    geometries = {"1": geometry(point(0, 0), point(1000, 0))}
    near, far, unknown = snap_sightings(
        [make_bus(100, 10), make_bus(200, 400), make_bus(300, 0, pattern_id="999")], geometries
    )
    assert near.on_route
    assert far.projection is not None and not far.on_route
    assert unknown.projection is None and not unknown.on_route


def test_degenerate_patterns_are_rejected_and_skipped():
    with pytest.raises(ValueError):
        geometry(point(0, 0), point(0, 0))
    route = Route.from_api(json.loads((FIXTURES / "route_22.json").read_text()))
    stub = route.model_copy(update={"patterns": [pattern(point(0, 0), pattern_id="x")]})
    assert pattern_geometries([stub]) == {}


def test_pattern_geometries_keys_by_pattern_id():
    route = Route.from_api(json.loads((FIXTURES / "route_22.json").read_text()))
    geometries = pattern_geometries([route])
    assert set(geometries) == {p.pattern_id for p in route.patterns}
    # Every stop on the pattern is kept, spur or not.
    for p in route.patterns:
        visible = [pt for pt in p.points if not pt.is_bypassed]
        assert len(geometries[p.pattern_id].stops) == sum(pt.stop is not None for pt in visible)
    regular = geometries["7599"]
    assert [s.dist_along_ft for s in regular.stops] == sorted(s.dist_along_ft for s in regular.stops)


def test_check_stop_distances_agrees_with_recorded_predictions():
    route = Route.from_api(json.loads((FIXTURES / "route_22.json").read_text()))
    buses = BusesForRouteResponse.from_api(
        json.loads((FIXTURES / "buses_for_route_22.json").read_text()), datetime(2026, 10, 19, 15, 37)
    ).buses
    predictions = PredictionsForBusResponse.from_api(
        json.loads((FIXTURES / "predictions_for_bus_466.json").read_text())
    ).predictions
    snapshot = RouteData(route_id="22", buses=buses, predictions={"466": predictions})

    checks = check_stop_distances([snapshot], pattern_geometries([route]))
    assert checks, "expected the recorded bus's predicted stops to be on its pattern"
    assert {c.vehicle_id for c in checks} == {"466"}
    for check in checks:
        assert abs(check.error_ft) < 25
