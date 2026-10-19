from datetime import datetime, timedelta

from aaata_bus import Bus, RouteData
from aaata_bus.visualization import load_route_data, plot_route_paths, trace_bus_paths

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
