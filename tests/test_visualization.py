import json
from datetime import datetime, timedelta
from pathlib import Path

from aaata_bus import Bus, Route, RouteData
from aaata_bus.schema import RoutePoint
from aaata_bus.visualization import load_route_data, plot_route_paths, trace_bus_paths
from aaata_bus.visualization.route_paths import _detour_class, _detour_runs

FIXTURES = Path(__file__).parent / "fixtures"

START = datetime(2026, 10, 18, 15, 0)


def make_bus(vehicle_id: str, minutes: int, lat: float) -> Bus:
    return Bus(
        vehicle_id=vehicle_id, consist=[], cars=[], rtpi_feed_name=[], mode="1",
        route_number="22", route_rtpi_feed_name=[], route_display="22",
        direction="North Bound", direction_destination="TO PPC", direction_code="N",
        latitude=lat, longitude=-83.7, pattern_id="1", pattern_destination="TO PPC",
        pattern_destination_rtpi_feed_name=[], run_number="1", destination_sign="To PPC",
        operator_id="1", dispatch_id="1", block_id="1", work_id_1="N/A", work_id_2="1",
        is_hidden=False, timestamp=START + timedelta(minutes=minutes),
    )


def snapshot(*buses: Bus) -> RouteData:
    return RouteData(route_id="22", buses=list(buses), predictions={})


def test_trace_bus_paths_orders_and_splits_on_gaps():
    snapshots = [
        snapshot(make_bus("561", 10, 42.2), make_bus("545", 10, 42.0)),
        snapshot(make_bus("561", 0, 42.1)),
        snapshot(make_bus("561", 60, 42.3)),  # long gap -> new path
    ]
    paths = trace_bus_paths(snapshots, max_gap=timedelta(minutes=15))

    assert [(p.vehicle_id, len(p.sightings)) for p in paths] == [("545", 1), ("561", 2), ("561", 1)]
    assert [lat for lat, _ in paths[1].coordinates] == [42.1, 42.2]


def test_load_route_data_round_trips(tmp_path):
    data_file = tmp_path / "data.jsonl"
    data_file.write_text(snapshot(make_bus("561", 0, 42.1)).model_dump_json() + "\n")

    [loaded] = load_route_data(data_file)
    assert loaded.buses[0].timestamp == START


def test_plot_route_paths_renders(tmp_path):
    paths = trace_bus_paths([snapshot(make_bus("561", 0, 42.1)), snapshot(make_bus("561", 5, 42.2))])
    out = tmp_path / "map.html"
    plot_route_paths(paths).save(out)
    assert "Route 22" in out.read_text()


def test_plot_route_paths_draws_route_shapes_and_stops(tmp_path):
    route = Route.from_api(json.loads((FIXTURES / "route_22.json").read_text()))
    stop = next(stop for pattern in route.patterns for stop in pattern.stops)

    out = tmp_path / "map.html"
    plot_route_paths([], routes=[route]).save(out)
    html = out.read_text()
    assert "Route 22" in html
    assert stop.stop_name in html
    assert "detour" in html  # the fixture's route has an active detour

    plot_route_paths([], routes=[route], show_stops=False).save(out)
    assert stop.stop_name not in out.read_text()


def test_detour_runs_are_not_joined():
    # A detoured pattern is the full detour path followed by the full regular
    # path; joining them would draw a line from the route's end back to its start.
    points = [
        RoutePoint(latitude=float(i), longitude=0.0, is_bypassed=d)
        for i, d in enumerate([False, False, True, True])
    ]
    runs = _detour_runs(points)
    assert [(d, [lat for lat, _ in c]) for d, c in runs] == [(False, [0, 1]), (True, [2, 3])]


def test_fixture_detour_starts_back_at_route_start():
    route = Route.from_api(json.loads((FIXTURES / "route_22.json").read_text()))
    pattern = next(p for p in route.patterns if any(pt.is_bypassed for pt in p.points))
    [(_, regular), (_, detour)] = _detour_runs(pattern.points)
    assert detour[0] == regular[0] and detour[-1] == regular[-1]


def test_plot_route_paths_tags_shapes_for_overlay_toggle(tmp_path):
    route = Route.from_api(json.loads((FIXTURES / "route_22.json").read_text()))
    paths = trace_bus_paths([snapshot(make_bus("561", 0, 42.1)), snapshot(make_bus("561", 5, 42.2))])
    out = tmp_path / "map.html"
    plot_route_paths(paths, routes=[route]).save(out)
    html = out.read_text()
    assert '"className": "route-shape"' in html
    assert '"className": "bus-ping ping-raw"' in html
    assert "overlay-toggle" in html
    # The fixture has an active detour, so both versions of that pattern are tagged.
    assert '"className": "route-shape detour-path"' in html
    assert '"className": "route-shape detour-regular"' in html
    assert "Detours (uncheck" in html


def test_plot_route_paths_omits_detour_toggle_without_detours(tmp_path):
    paths = trace_bus_paths([snapshot(make_bus("561", 0, 42.1))])
    out = tmp_path / "map.html"
    plot_route_paths(paths).save(out)
    assert "Detours (uncheck" not in out.read_text()


def test_detour_class_treats_dtr_points_as_the_bypassed_regular_path():
    # Checked against route 22: its `dtr: true` points are the usual route the
    # detour avoids, and the `dtr: false` points are where buses actually go.
    assert _detour_class(is_bypassed=False, pattern_detoured=True) == "detour-path"
    assert _detour_class(is_bypassed=True, pattern_detoured=True) == "detour-regular"
    assert _detour_class(is_bypassed=False, pattern_detoured=False) == ""


def route_22_bus(lat: float, lon: float, minutes: int) -> Bus:
    """A bus on the fixture's undetoured TO PPC pattern."""
    return make_bus("561", minutes, lat).model_copy(update={"longitude": lon, "pattern_id": "7599"})


def test_plot_route_paths_draws_raw_and_snapped_pings(tmp_path):
    route = Route.from_api(json.loads((FIXTURES / "route_22.json").read_text()))
    stop_point = next(p for p in route.patterns if p.pattern_id == "7599").points[10]
    paths = trace_bus_paths([
        snapshot(route_22_bus(stop_point.latitude + 0.0001, stop_point.longitude, 0)),
        snapshot(route_22_bus(stop_point.latitude, stop_point.longitude + 0.0001, 1)),
        snapshot(route_22_bus(42.0, -83.0, 2)),  # far off the route
    ])
    out = tmp_path / "map.html"
    plot_route_paths(paths, routes=[route]).save(out)
    html = out.read_text()

    assert '"className": "bus-ping ping-raw"' in html
    assert html.count('"className": "bus-ping ping-snapped"') == 3  # snapped pings: markers only
    assert "too far to snap" in html
    assert "ft along pattern 7599" in html
    assert 'radio.checked = view[0] === "raw"' in html
    assert 'classList.toggle("snapped-pings", false)' in html

    plot_route_paths(paths, routes=[route], ping_view="snapped").save(out)
    assert 'classList.toggle("snapped-pings", true)' in out.read_text()


def test_snapped_pings_need_route_shapes(tmp_path):
    paths = trace_bus_paths([snapshot(make_bus("561", 0, 42.1)), snapshot(make_bus("561", 5, 42.2))])
    out = tmp_path / "map.html"
    plot_route_paths(paths, ping_view="snapped").save(out)
    html = out.read_text()
    assert "ping-snapped" not in html.replace(".ping-snapped", "")
    assert '"className": "bus-ping"' in html
    assert "snapped to route path" not in html
