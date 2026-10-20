"""Offline API tests, run against recorded TheRide responses in tests/fixtures/."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import aaata_bus.api as api
from aaata_bus.api import (
    get_buses_for_route,
    get_buses_for_route_async,
    get_predictions_for_bus,
    get_predictions_for_bus_async,
    get_route,
    get_route_async,
    get_service_bulletins,
    get_service_bulletins_async,
)
from aaata_bus.definitions import USER_AGENT
from aaata_bus.schema import Bus, BusPrediction, Route, ServiceBulletin

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def fake_get(payload: Any, status_code: int = 200):
    """Stand-in for httpx.get that returns `payload` for any URL."""

    def get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))

    return get


def fake_client(payload: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))


def test_sync_requests_send_user_agent(monkeypatch):
    sent: list[dict[str, str] | None] = []

    def get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        sent.append(headers)
        return httpx.Response(200, json={"buses": {"bus": None}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(api.httpx, "get", get)
    get_buses_for_route("22")
    assert sent == [{"User-Agent": USER_AGENT}]


def test_async_requests_send_user_agent():
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers["User-Agent"])
        return httpx.Response(200, json={"buses": {"bus": None}})

    async def run() -> None:
        # A caller's own client, without our headers, still identifies us.
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await get_buses_for_route_async(client, "22")

    asyncio.run(run())
    assert sent == [USER_AGENT]
    assert "adempst.dev@gmail.com" in USER_AGENT


def test_get_buses_for_route(monkeypatch):
    monkeypatch.setattr(api.httpx, "get", fake_get(load_fixture("buses_for_route_22.json")))
    buses = get_buses_for_route("22")

    assert buses is not None
    assert [bus.vehicle_id for bus in buses] == ["466", "495"]
    bus = buses[0]
    assert isinstance(bus, Bus)
    assert bus.route_number == "22"
    assert bus.latitude == pytest.approx(42.2973595)
    assert bus.is_hidden is False
    assert bus.work_id_1 == "N/A"


def test_get_buses_for_route_no_buses(monkeypatch):
    monkeypatch.setattr(api.httpx, "get", fake_get({"buses": {"bus": None}}))
    assert get_buses_for_route("22") == []


def test_get_buses_for_route_single_bus(monkeypatch):
    # The API returns a bare object rather than a one-element list.
    payload = load_fixture("buses_for_route_22.json")
    payload["buses"]["bus"] = payload["buses"]["bus"][0]
    monkeypatch.setattr(api.httpx, "get", fake_get(payload))

    buses = get_buses_for_route("22")
    assert buses is not None
    assert [bus.vehicle_id for bus in buses] == ["466"]


def test_get_buses_for_route_http_error(monkeypatch):
    monkeypatch.setattr(api.httpx, "get", fake_get({}, status_code=500))
    with pytest.raises(httpx.HTTPStatusError):
        get_buses_for_route("22")


def test_get_predictions_for_bus(monkeypatch):
    monkeypatch.setattr(api.httpx, "get", fake_get(load_fixture("predictions_for_bus_466.json")))
    predictions = get_predictions_for_bus("466")

    assert predictions is not None
    assert len(predictions) == 2
    prediction = predictions[0]
    assert isinstance(prediction, BusPrediction)
    assert prediction.vehicle_id == "466"
    assert prediction.stop_sequence == 4
    assert prediction.is_flag_stop is False
    assert prediction.predicted_arrival_at.isoformat() == "2026-10-19T15:38:00-04:00"


def test_get_predictions_for_bus_no_data(monkeypatch):
    payload = {"bustime-response": {"error": [{"vid": "466", "msg": "No data found for parameter"}]}}
    monkeypatch.setattr(api.httpx, "get", fake_get(payload))
    assert get_predictions_for_bus("466") == []


def test_async_fetchers():
    async def run() -> tuple[list[Bus] | None, list[BusPrediction] | None]:
        async with fake_client(load_fixture("buses_for_route_22.json")) as client:
            buses = await get_buses_for_route_async(client, "22")
        async with fake_client(load_fixture("predictions_for_bus_466.json")) as client:
            predictions = await get_predictions_for_bus_async(client, "466")
        return buses, predictions

    buses, predictions = asyncio.run(run())
    assert buses is not None and len(buses) == 2
    assert predictions is not None and len(predictions) == 2


def test_get_route(monkeypatch):
    monkeypatch.setattr(api.httpx, "get", fake_get(load_fixture("route_22.json")))
    route = get_route("22")

    assert isinstance(route, Route)
    assert route.route_id == "22"
    assert route.name == "Pontiac - Dhu Varren"
    assert route.color == "#6666ff"
    assert [p.pattern_id for p in route.patterns] == ["501321", "7599"]

    pattern = route.pattern("501321")
    assert pattern is not None
    assert pattern.direction == "TO BTC"
    assert len(pattern.points) == 421
    assert pattern.points[0].latitude == pytest.approx(42.290439)
    assert pattern.points[0].is_bypassed is False
    assert any(point.is_bypassed for point in pattern.points)
    assert len(pattern.stops) == 75
    assert pattern.stops[0].stop_id == "1714"
    assert pattern.stops[0].stop_name == "Bonisteel + Murfin"
    assert route.pattern("nope") is None

    # The fixture has a single detour and bulletin, sent as bare objects.
    assert len(route.detours) == 1
    detour = route.detours[0]
    assert [d.direction for d in detour.directions] == ["TO BTC"]
    assert detour.starts_at.isoformat() == "2026-08-31T01:00:00-04:00"
    assert detour.ends_at.isoformat() == "2026-10-24T00:00:00-04:00"
    assert [b.name for b in route.service_bulletins] == ["Rt 22 NB Nixon Detour"]


def test_get_route_unknown_id(monkeypatch):
    # Unknown ids return a stub with `[]` in place of the name and patterns.
    payload = {"route": {"id": "9999", "rtpiFeedName": [], "sn": "9999", "nm": [], "displayDesignator": "9999",
                         "vmode": "1", "sbs": [], "c": "#c8c8c8", "pas": []}}
    monkeypatch.setattr(api.httpx, "get", fake_get(payload))
    route = get_route("9999")

    assert route.name is None
    assert route.patterns == []
    assert route.detours == []
    assert route.service_bulletins == []


def test_route_unescapes_html():
    payload = load_fixture("route_22.json")
    payload["route"]["pas"]["pa"][0]["dd"] = "to Green Rd P&amp;R"
    route = Route.from_api(payload)
    assert route.patterns[0].direction_destination == "to Green Rd P&R"


def test_route_round_trips():
    route = Route.from_api(load_fixture("route_22.json"))
    assert Route.model_validate_json(route.model_dump_json(by_alias=True)) == route


def test_get_route_async():
    async def run() -> Route:
        async with fake_client(load_fixture("route_22.json")) as client:
            return await get_route_async(client, "22")

    assert asyncio.run(run()).route_id == "22"


def test_get_service_bulletins(monkeypatch):
    urls: list[str] = []
    payload = load_fixture("service_bulletins_22.json")

    def get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        urls.append(url)
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(api.httpx, "get", get)
    bulletins = get_service_bulletins("22")

    assert urls[0].endswith("method=getservicebulletins&routeid=22")
    assert [b.name for b in bulletins] == [
        "Football Detours 2026",
        "4th Ave/BTC Temporary Closure Starts May 3",
        "Rt 22 NB Nixon Detour",
    ]
    # System-wide bulletins come back alongside route-specific ones.
    assert [b.is_system_wide for b in bulletins] == [True, True, False]
    nixon = bulletins[2]
    assert nixon.route_numbers == ["22"]
    assert nixon.services[0].stop_id == ""
    assert nixon.cause == "Construction"
    assert nixon.effect == "Detour"
    assert nixon.modified_at.isoformat() == "2026-08-28T11:24:02-04:00"

    get_service_bulletins()
    assert urls[1].endswith("method=getservicebulletins")


def test_get_service_bulletins_none(monkeypatch):
    # Unknown route ids, or no bulletins, give a bare [].
    monkeypatch.setattr(api.httpx, "get", fake_get([]))
    assert get_service_bulletins("9999") == []


def test_service_bulletin_round_trips():
    bulletin = ServiceBulletin.model_validate(load_fixture("service_bulletins_22.json")[2]["sb"])
    assert ServiceBulletin.model_validate_json(bulletin.model_dump_json(by_alias=True)) == bulletin


def test_get_service_bulletins_async():
    async def run() -> list[ServiceBulletin]:
        async with fake_client(load_fixture("service_bulletins_22.json")) as client:
            return await get_service_bulletins_async(client, "22")

    assert len(asyncio.run(run())) == 3


def test_get_routes_skips_failures_with_a_warning(monkeypatch):
    payload = load_fixture("route_22.json")

    def get(url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        status = 500 if "id=99" in url else 200
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(api.httpx, "get", get)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    with pytest.warns(UserWarning, match="route 99"):
        routes = api.get_routes(["22", "99"])
    assert [route.route_id for route in routes] == ["22"]
