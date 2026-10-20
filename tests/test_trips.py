"""Trip reconstruction tests, on synthetic patterns laid out in feet."""

from datetime import date, datetime, timedelta, timezone
from math import cos, degrees, radians
from zoneinfo import ZoneInfo

import pytest

from aaata_bus import RouteData
from aaata_bus.geometry import EARTH_RADIUS_FT, PatternGeometry
from aaata_bus.schema import Bus, BusPrediction, RoutePattern, RoutePoint, RouteStop
from aaata_bus.trips import (
    TRIP_PING_COLUMNS,
    isotonic_fit,
    reconstruct_trips,
    trip_pings_rows,
)
from aaata_bus.times import observation_time, to_utc
from aaata_bus.visualization import plot_trips

LAT0, LON0 = 42.28, -83.74
START = datetime(2026, 10, 19, 16, 0)  # naive, as on the collector's (UTC) clock
LENGTH_FT = 10_000


def latlon(x_ft: float, y_ft: float = 0.0) -> tuple[float, float]:
    lat = LAT0 + degrees(y_ft / EARTH_RADIUS_FT)
    lon = LON0 + degrees(x_ft / (EARTH_RADIUS_FT * cos(radians(LAT0))))
    return lat, lon


def straight_pattern(pattern_id: str, reverse: bool = False) -> PatternGeometry:
    """A 10,000 ft east-west pattern with stops every 2,500 ft, run west to east
    (or east to west with `reverse`)."""
    xs = range(0, LENGTH_FT + 1, 2500)
    points = [
        RoutePoint(
            latitude=latlon(x)[0], longitude=latlon(x)[1], is_bypassed=False,
            stop=RouteStop(stop_id=f"s{x}", stop_name=f"Stop {x}"),
        )
        for x in (reversed(xs) if reverse else xs)
    ]
    pattern = RoutePattern(
        pattern_id=pattern_id, direction="EB", direction_destination="EB", points=points
    )
    return PatternGeometry("22", pattern)


GEOMETRIES = {"E": straight_pattern("E"), "W": straight_pattern("W", reverse=True)}


def make_bus(x_ft: float, pattern_id: str = "E", y_ft: float = 0.0, vehicle_id: str = "561") -> Bus:
    lat, lon = latlon(x_ft, y_ft)
    return Bus(
        vehicle_id=vehicle_id, consist=[], cars=[], rtpi_feed_name=[], mode="1",
        route_number="22", route_rtpi_feed_name=[], route_display="22",
        direction="East Bound", direction_destination="TO BTC", direction_code="E",
        latitude=lat, longitude=lon, pattern_id=pattern_id, pattern_destination="TO BTC",
        pattern_destination_rtpi_feed_name=[], run_number="1081", destination_sign="To BTC",
        operator_id="1619", dispatch_id="1", block_id="4402", work_id_1="N/A", work_id_2="1",
        is_hidden=False, timestamp=START,
    )


def prediction(stop_id: str, distance_ft: int, trip_id: str, vehicle_id: str = "561") -> BusPrediction:
    stamp = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo("America/Detroit"))
    return BusPrediction(
        prediction_generated_at=stamp, prediction_type="A", stop_name="Stop", stop_id=stop_id,
        vehicle_id=vehicle_id, distance_to_stop_ft=distance_ft, route_number="22",
        route_display="22", route_direction="TO BTC", destination="BTC",
        predicted_arrival_at=stamp, schedule_block_id="300730", scheduled_trip_id=f"sched-{trip_id}",
        original_scheduled_trip_number="1", is_delayed=False, dynamic_code=0, countdown="5",
        fare_zone="", run_id="1081", trip_id=trip_id, trip_dynamic_code=0, scheduled_time=stamp,
        geo_id="1", stop_sequence=1, passenger_load="", trip_start_seconds=43200,
        service_date=date(2026, 10, 19), is_flag_stop=False,
    )


def snap(seconds: float, *buses: Bus, predictions: dict[str, list[BusPrediction]] | None = None) -> RouteData:
    return RouteData(
        route_id="22",
        buses=list(buses),
        predictions=predictions or {},
        predictions_collected=predictions is not None,
        requested_at=START + timedelta(seconds=seconds),
    )


def run(*xs: float, pattern_id: str = "E", t0: float = 0.0, step_s: float = 30.0) -> list[RouteData]:
    """Snapshots of one bus at each distance east of the origin, `step_s` apart."""
    return [snap(t0 + i * step_s, make_bus(x, pattern_id)) for i, x in enumerate(xs)]


def test_isotonic_fit():
    assert isotonic_fit([]) == []
    assert isotonic_fit([1, 2, 3]) == [1, 2, 3]
    assert isotonic_fit([1, 3, 2, 4]) == [1, 2.5, 2.5, 4]
    assert isotonic_fit([5, 1, 1, 1]) == [2, 2, 2, 2]


def test_times_are_normalized_to_utc():
    assert to_utc(START) == START.replace(tzinfo=timezone.utc)
    detroit = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo("America/Detroit"))
    assert to_utc(detroit) == START.replace(tzinfo=timezone.utc)
    assert to_utc(START, naive_tz=ZoneInfo("America/Detroit")) == datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc)

    bus = make_bus(0).model_copy(update={"timestamp": START + timedelta(seconds=5)})
    with_request = snap(0, bus)
    legacy = with_request.model_copy(update={"requested_at": None})
    assert observation_time(with_request, bus) == START.replace(tzinfo=timezone.utc)
    assert observation_time(legacy, bus) == (START + timedelta(seconds=5)).replace(tzinfo=timezone.utc)


def test_one_trip_along_a_pattern():
    [trip] = reconstruct_trips(run(0, 500, 1200, 2000), GEOMETRIES)
    assert (trip.vehicle_id, trip.pattern_id, trip.route_id) == ("561", "E", "22")
    assert [round(p.dist_ft) for p in trip.pings] == [0, 500, 1200, 2000]
    assert trip.start == START.replace(tzinfo=timezone.utc)
    assert trip.trip_key == "561-E-20261019T160000Z"
    assert trip.trip_id is None


def test_pattern_change_starts_a_new_trip():
    snapshots = run(8000, 9500, 10000) + run(10000, 9000, 7000, pattern_id="W", t0=90)
    eastbound, westbound = reconstruct_trips(snapshots, GEOMETRIES)
    assert [round(p.dist_ft) for p in eastbound.pings] == [8000, 9500, 10000]
    # The westbound pattern starts where the eastbound one ended.
    assert westbound.pattern_id == "W"
    assert [round(p.dist_ft) for p in westbound.pings] == [0, 1000, 3000]


def test_distance_reset_starts_a_new_trip():
    first, second = reconstruct_trips(run(7000, 9000, 9900, 50, 600), GEOMETRIES)
    assert len(first.pings) == 3 and len(second.pings) == 2
    assert second.pings[0].dist_ft == pytest.approx(50)


def test_small_backward_jitter_is_smoothed_not_split():
    [trip] = reconstruct_trips(run(1000, 2000, 1800, 2600), GEOMETRIES)
    assert [round(p.raw_dist_ft) for p in trip.pings] == [1000, 2000, 1800, 2600]
    assert [round(p.dist_ft) for p in trip.pings] == [1000, 1900, 1900, 2600]


def test_gap_splits_and_merge_rejoins_forward_progress():
    later = 20 * 60  # 20 minutes on: past max_gap, within merge_gap
    carries_on = run(1000, 1500) + run(4000, 4500, t0=later)
    [trip] = reconstruct_trips(carries_on, GEOMETRIES)
    assert len(trip.pings) == 4

    starts_over = run(8000, 8500) + run(0, 500, t0=later)
    assert len(reconstruct_trips(starts_over, GEOMETRIES)) == 2

    too_long = run(1000, 1500) + run(4000, 4500, t0=60 * 60)
    assert len(reconstruct_trips(too_long, GEOMETRIES)) == 2


def test_merge_respects_disagreeing_trip_ids():
    later = 20 * 60
    snapshots = [
        snap(0, make_bus(1000), predictions={"561": [prediction("s2500", 1500, "A")]}),
        snap(30, make_bus(1500)),
        snap(later, make_bus(4000), predictions={"561": [prediction("s5000", 1000, "B")]}),
    ]
    assert len(reconstruct_trips(snapshots, GEOMETRIES)) == 2


def test_off_route_pings_are_left_out_without_splitting():
    snapshots = run(0, 500) + [snap(60, make_bus(1000, y_ft=600))] + run(1500, t0=90)
    [trip] = reconstruct_trips(snapshots, GEOMETRIES)
    assert [round(p.dist_ft) for p in trip.pings] == [0, 500, 1500]


def test_unknown_patterns_are_left_out():
    assert reconstruct_trips(run(0, 500, pattern_id="nope"), GEOMETRIES) == []


def test_repeats_are_kept_unless_the_bus_must_have_moved():
    # Stopped for a minute at 2,000 ft, then moving at a normal speed: a layover.
    [layover] = reconstruct_trips(run(1500, 2000, 2000, 2000, 2600), GEOMETRIES)
    assert [p.is_repeat for p in layover.pings] == [False, False, True, True, False]
    assert layover.stale_pings == []

    # Frozen at 2,000 ft, then 4,000 ft in 30 s (133 ft/s): the position was stale.
    [frozen] = reconstruct_trips(run(1500, 2000, 2000, 2000, 6000), GEOMETRIES)
    assert [round(p.raw_dist_ft) for p in frozen.pings] == [1500, 2000, 6000]
    assert len(frozen.stale_pings) == 2


def test_trip_ids_come_from_predictions_for_the_pattern():
    snapshots = [
        snap(0, make_bus(1000), predictions={"561": [prediction("s2500", 1500, "T1"), prediction("s5000", 4000, "T1")]}),
        snap(30, make_bus(1500)),
        # At the end, the nearest predicted stop can be the next trip's; it only
        # counts if that stop is on this pattern.
        snap(60, make_bus(2400), predictions={"561": [prediction("elsewhere", 10, "T2")]}),
    ]
    [trip] = reconstruct_trips(snapshots, GEOMETRIES)
    assert trip.trip_id == "T1"
    assert dict(trip.trip_id_votes) == {"T1": 1}
    assert trip.scheduled_trip_id == "sched-T1"
    assert trip.service_date == date(2026, 10, 19)
    assert trip.trip_start_seconds == 43200


def test_vehicles_are_tracked_separately():
    snapshots = [
        snap(i * 30, make_bus(1000 + 500 * i), make_bus(9000 - 500 * i, "W", vehicle_id="600"))
        for i in range(3)
    ]
    trips = reconstruct_trips(snapshots, GEOMETRIES)
    assert [(t.vehicle_id, t.pattern_id, len(t.pings)) for t in trips] == [("561", "E", 3), ("600", "W", 3)]


def test_trip_pings_rows():
    snapshots = [snap(0, make_bus(1000), predictions={"561": [prediction("s2500", 1500, "T1")]})] + run(1500, t0=30)
    rows = trip_pings_rows(reconstruct_trips(snapshots, GEOMETRIES))
    assert len(rows) == 2
    assert list(rows[0]) == TRIP_PING_COLUMNS
    assert rows[0]["t"] == "2026-10-19T16:00:00+00:00"
    assert rows[1]["dist_ft"] == 1500.0
    assert (rows[0]["trip_id"], rows[0]["service_date"], rows[0]["operator_id"]) == ("T1", "2026-10-19", "1619")


def test_plot_trips_renders(tmp_path):
    trips = reconstruct_trips(run(1500, 2000, 2000, 2000, 6000) + run(0, 3000, pattern_id="W", t0=300), GEOMETRIES)
    out = tmp_path / "trips.html"
    plot_trips(trips).write_html(out, include_plotlyjs=False)
    html = out.read_text()
    assert "pattern E" in html and "pattern W" in html
    for name in ("Isotonic fit", "Raw pings", "Dropped stale pings", "Stops"):
        assert name in html
