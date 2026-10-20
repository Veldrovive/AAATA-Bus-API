"""Saved route versions: deduplicated saving, and picking the version a sighting belongs to."""

import asyncio
import copy
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

import aaata_bus.route_history as route_history
from aaata_bus import RouteData
from aaata_bus.route_history import (
    RouteHistory,
    RouteVersion,
    load_route_history,
    payload_hash,
    save_route_version,
)
from aaata_bus.schema import Bus
from aaata_bus.trips import TRIP_PING_COLUMNS, reconstruct_trips, trip_pings_rows

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPTS = Path(__file__).parent.parent / "scripts"

T1 = datetime(2026, 10, 19, 12, 0)  # naive, as on the collector's (UTC) clock
T2 = datetime(2026, 10, 20, 12, 0)


def route_22() -> dict[str, Any]:
    return json.loads((FIXTURES / "route_22.json").read_text())


def changed_route_22() -> dict[str, Any]:
    """Route 22 after its detour ends: the detour pattern is gone and the
    regular TO PPC pattern's first point has moved 0.001° north."""
    payload = copy.deepcopy(route_22())
    patterns = payload["route"]["pas"]["pa"]
    payload["route"]["pas"]["pa"] = [p for p in patterns if p["id"] == "7599"]
    first = payload["route"]["pas"]["pa"][0]["pt"][0]
    first["lat"] = str(float(first["lat"]) + 0.001)
    return payload


def history_of_two() -> tuple[RouteHistory, RouteVersion, RouteVersion]:
    v1 = RouteVersion.from_payload("22", route_22(), T1)
    v2 = RouteVersion.from_payload("22", changed_route_22(), T2)
    return RouteHistory([v2, v1]), v1, v2


def test_payload_hash_ignores_key_order():
    assert payload_hash({"a": 1, "b": [1, 2]}) == payload_hash({"b": [1, 2], "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


def reordered_route_66_style(payload: dict[str, Any]) -> dict[str, Any]:
    """The same route with its patterns listed in another order, as the API
    sometimes returns them."""
    payload = copy.deepcopy(payload)
    payload["route"]["pas"]["pa"].reverse()
    return payload


def test_payload_hash_ignores_pattern_order_but_not_point_order():
    assert payload_hash(reordered_route_66_style(route_22())) == payload_hash(route_22())

    reversed_points = copy.deepcopy(route_22())
    reversed_points["route"]["pas"]["pa"][0]["pt"].reverse()
    assert payload_hash(reversed_points) != payload_hash(route_22())


def test_payload_hash_handles_collection_quirks():
    # Single-object and empty `[]` collections hash without error, and still differ.
    single = {"route": {"id": "1", "pas": {"pa": {"id": "a", "pt": []}}, "dtrs": [], "sbs": {"sb": {"nm": "x"}}}}
    empty = {"route": {"id": "1", "pas": [], "sbs": []}}
    assert payload_hash(single) != payload_hash(empty)
    assert payload_hash([]) == payload_hash([])


def test_route_version_round_trips():
    version = RouteVersion.from_payload("22", route_22(), T1)
    assert version.route_version == f"22-{version.content_hash[:12]}"
    assert version.route.route_id == "22"

    loaded = RouteVersion.model_validate_json(version.model_dump_json())
    assert loaded == version
    assert loaded.route.pattern("7599") is not None


def test_save_route_version_skips_unchanged_content(tmp_path):
    path = tmp_path / "route_versions.jsonl"
    history = RouteHistory()
    original = RouteVersion.from_payload("22", route_22(), T1)
    assert save_route_version(original, history, path)
    assert not save_route_version(RouteVersion.from_payload("22", route_22(), T1 + timedelta(hours=1)), history, path)
    assert save_route_version(RouteVersion.from_payload("22", changed_route_22(), T2), history, path)
    # Changing back saves again: it differs from the previous version. Same content, same id.
    reverted = RouteVersion.from_payload("22", route_22(), T2 + timedelta(hours=1))
    assert save_route_version(reverted, history, path)
    assert reverted.route_version == original.route_version

    lines = path.read_text().splitlines()
    assert len(lines) == 3
    # After a restart, the file is what "previous" is checked against.
    reloaded = RouteHistory.load(path)
    assert [v.fetched_at for v in reloaded.versions("22")] == [T1, T2, T2 + timedelta(hours=1)]
    assert not save_route_version(RouteVersion.from_payload("22", route_22(), T2 + timedelta(hours=2)), reloaded, path)
    assert len(path.read_text().splitlines()) == 3


def test_reordered_patterns_are_not_a_new_version(tmp_path):
    path = tmp_path / "route_versions.jsonl"
    history = RouteHistory()
    assert save_route_version(RouteVersion.from_payload("22", route_22(), T1), history, path)
    reordered = RouteVersion.from_payload("22", reordered_route_66_style(route_22()), T2)
    assert not save_route_version(reordered, history, path)
    assert len(path.read_text().splitlines()) == 1


def test_lines_with_an_old_style_hash_still_compare_by_content(tmp_path):
    # Saved before patterns were sorted for hashing: the stored hash is of the raw order.
    path = tmp_path / "route_versions.jsonl"
    legacy = RouteVersion.from_payload("22", route_22(), T1).model_copy(
        update={"content_hash": "0" * 64, "route_version": "22-000000000000"}
    )
    path.write_text(legacy.model_dump_json() + "\n")
    history = RouteHistory.load(path)
    assert not save_route_version(RouteVersion.from_payload("22", reordered_route_66_style(route_22()), T2), history, path)
    assert save_route_version(RouteVersion.from_payload("22", changed_route_22(), T2), history, path)


def test_missing_file_is_an_empty_history(tmp_path):
    assert RouteHistory.load(tmp_path / "nope.jsonl").route_ids == set()


def test_version_at_matches_pattern_then_time():
    history, v1, v2 = history_of_two()
    assert history.latest("22") == v2

    # The regular pattern is in both versions: pick by time.
    assert history.version_at("22", "7599", T1 - timedelta(days=1)) == v1  # before any: the earliest
    assert history.version_at("22", "7599", T1 + timedelta(hours=1)) == v1
    assert history.version_at("22", "7599", T2 + timedelta(hours=1)) == v2
    # Aware times compare correctly with the naive (UTC) fetch times.
    assert history.version_at("22", "7599", (T2 - timedelta(minutes=1)).replace(tzinfo=timezone.utc)) == v1
    # The detour pattern is only in the first version, so it's used even after the second.
    assert history.version_at("22", "501321", T2 + timedelta(hours=1)) == v1
    assert history.version_at("22", "nope", T1) is None
    assert history.version_at("63", "7599", T1) is None


def test_geometry_is_tagged_and_cached():
    history, v1, v2 = history_of_two()
    early = history.geometry("22", "7599", T1)
    late = history.geometry("22", "7599", T2)
    assert early is not None and late is not None
    assert (early.route_version, late.route_version) == (v1.route_version, v2.route_version)
    assert early.length_ft != pytest.approx(late.length_ft)
    assert history.geometry("22", "7599", T1 + timedelta(minutes=5)) is early


def bus_at(lat: float, lon: float, t: datetime) -> Bus:
    return Bus(
        vehicle_id="561", consist=[], cars=[], rtpi_feed_name=[], mode="1",
        route_number="22", route_rtpi_feed_name=[], route_display="22",
        direction="South Bound", direction_destination="TO PPC", direction_code="S",
        latitude=lat, longitude=lon, pattern_id="7599", pattern_destination="TO PPC",
        pattern_destination_rtpi_feed_name=[], run_number="1", destination_sign="To PPC",
        operator_id="1", dispatch_id="1", block_id="1", work_id_1="N/A", work_id_2="1",
        is_hidden=False, timestamp=t,
    )


def test_trips_snap_to_the_version_in_effect_and_split_at_a_change():
    history, v1, v2 = history_of_two()
    pattern = v1.route.pattern("7599")
    assert pattern is not None
    points = pattern.points
    # One bus moving along the pattern, polled across the moment v2 was fetched.
    times = [T2 - timedelta(seconds=60), T2 - timedelta(seconds=30), T2 + timedelta(seconds=1), T2 + timedelta(seconds=30)]
    snapshots = [
        RouteData(route_id="22", buses=[bus_at(p.latitude, p.longitude, t)], predictions={}, requested_at=t)
        for p, t in zip(points[20:60:10], times)
    ]
    before, after = reconstruct_trips(snapshots, history)
    assert (before.route_version, len(before.pings)) == (v1.route_version, 2)
    assert (after.route_version, len(after.pings)) == (v2.route_version, 2)

    rows = trip_pings_rows([before, after])
    assert "route_version" in TRIP_PING_COLUMNS
    assert [r["route_version"] for r in rows] == [v1.route_version] * 2 + [v2.route_version] * 2


def test_load_route_history_fetches_only_missing_routes(tmp_path, monkeypatch):
    path = tmp_path / "route_versions.jsonl"
    save_route_version(RouteVersion.from_payload("22", route_22(), T1), RouteHistory(), path)
    fetched = []

    def fake_payload(route_id: str) -> dict[str, Any]:
        fetched.append(route_id)
        payload = copy.deepcopy(route_22())
        payload["route"]["id"] = route_id
        return payload

    monkeypatch.setattr(route_history, "get_route_payload", fake_payload)
    monkeypatch.setattr(route_history.time, "sleep", lambda _: None)

    history = load_route_history(["22", "63"], path)
    assert fetched == ["63"]
    assert history.route_ids == {"22", "63"}
    assert [v.fetched_at for v in history.versions("22")] == [T1]  # the saved one, not refetched
    assert len(path.read_text().splitlines()) == 1  # live fetches aren't saved

    assert load_route_history(["22", "63"], path, fetch_missing=False).route_ids == {"22"}


def load_collect_data_script():
    spec = importlib.util.spec_from_file_location("collect_data", SCRIPTS / "collect_data.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collector_saves_only_changed_route_versions(tmp_path, monkeypatch):
    collect_data = load_collect_data_script()
    path = tmp_path / "route_versions.jsonl"
    monkeypatch.setattr(collect_data, "ROUTE_VERSIONS_FILE", path)
    monkeypatch.setattr(collect_data, "courtesy_wait", lambda: asyncio.sleep(0))
    payloads = {"22": route_22()}
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payloads[request.url.params["routeid"]])
    ))

    history = RouteHistory()
    asyncio.run(collect_data.save_route_versions(client, [22], history))
    asyncio.run(collect_data.save_route_versions(client, [22], history))
    assert len(path.read_text().splitlines()) == 1

    payloads["22"] = changed_route_22()
    asyncio.run(collect_data.save_route_versions(client, [22], history))
    saved = [RouteVersion.model_validate_json(line) for line in path.read_text().splitlines()]
    assert [v.content_hash for v in saved] == [payload_hash(route_22()), payload_hash(changed_route_22())]
    assert saved[0].fetched_at < saved[1].fetched_at
