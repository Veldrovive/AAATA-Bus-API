import random
import time
import warnings
from collections.abc import Iterable
from datetime import datetime

import httpx

from aaata_bus.definitions import (
    BUSSES_FOR_ROUTE_URL, PREDICTIONS_FOR_BUS_URL, ROUTE_BY_ID_URL, SERVICE_BULLETINS_URL,
)
from aaata_bus.schema import (
    BusesForRouteResponse, BusPrediction,
    Bus, PredictionsForBusResponse, Route,
    ServiceBulletin, ServiceBulletinsResponse,
)

def _parse_buses_response(response: httpx.Response) -> list[Bus] | None:
    response.raise_for_status()
    timestamp = datetime.now()
    busses_response = BusesForRouteResponse.from_api(response.json(), timestamp)

    # convert to list of buses
    return busses_response.buses

def _parse_predictions_response(response: httpx.Response) -> list[BusPrediction] | None:
    response.raise_for_status()
    predictions_response = PredictionsForBusResponse.from_api(response.json())

    # convert to list of predictions
    return predictions_response.predictions

def _parse_route_response(response: httpx.Response) -> Route:
    response.raise_for_status()
    return Route.from_api(response.json())

def _parse_service_bulletins_response(response: httpx.Response) -> list[ServiceBulletin]:
    response.raise_for_status()
    return ServiceBulletinsResponse.from_api(response.json()).bulletins

def _service_bulletins_url(route_id: str | None) -> str:
    if route_id is None:
        return SERVICE_BULLETINS_URL
    return f"{SERVICE_BULLETINS_URL}&routeid={route_id}"

def get_buses_for_route(route_id: str) -> list[Bus] | None:
    """Get all buses for a specific route.

    Args:
        route_id: The ID of the route to get buses for.

    Returns:
        A BusesResponse object containing the buses for the route.
    """
    url = BUSSES_FOR_ROUTE_URL.format(route_id=route_id)
    return _parse_buses_response(httpx.get(url))

def get_predictions_for_bus(vehicle_id: str) -> list[BusPrediction] | None:
    """Get all predictions for a specific bus.

    Args:
        vehicle_id: The ID of the bus to get predictions for.

    Returns:
        A PredictionsResponse object containing the predictions for the bus.
    """
    url = PREDICTIONS_FOR_BUS_URL.format(vehicle_id=vehicle_id)
    return _parse_predictions_response(httpx.get(url))

def get_route(route_id: str) -> Route:
    """Get a route's description: name, colour, pattern paths with stops, detours
    and service bulletins.

    Args:
        route_id: The ID of the route, e.g. "22".

    Returns:
        The Route. An unknown route id gives a stub with no name or patterns.
    """
    url = ROUTE_BY_ID_URL.format(route_id=route_id)
    return _parse_route_response(httpx.get(url))

def get_service_bulletins(route_id: str | None = None) -> list[ServiceBulletin]:
    """Get rider-facing service bulletins (detours, closures, notices).

    Args:
        route_id: Limit to bulletins for this route, plus system-wide ones.
            None gets every bulletin.

    Returns:
        The bulletins; empty if there are none or the route id is unknown.
    """
    return _parse_service_bulletins_response(httpx.get(_service_bulletins_url(route_id)))

async def get_buses_for_route_async(client: httpx.AsyncClient, route_id: str) -> list[Bus] | None:
    """Async version of get_buses_for_route using a shared client."""
    url = BUSSES_FOR_ROUTE_URL.format(route_id=route_id)
    return _parse_buses_response(await client.get(url))

async def get_predictions_for_bus_async(client: httpx.AsyncClient, vehicle_id: str) -> list[BusPrediction] | None:
    """Async version of get_predictions_for_bus using a shared client."""
    url = PREDICTIONS_FOR_BUS_URL.format(vehicle_id=vehicle_id)
    return _parse_predictions_response(await client.get(url))

async def get_route_async(client: httpx.AsyncClient, route_id: str) -> Route:
    """Async version of get_route using a shared client."""
    url = ROUTE_BY_ID_URL.format(route_id=route_id)
    return _parse_route_response(await client.get(url))

async def get_service_bulletins_async(
    client: httpx.AsyncClient, route_id: str | None = None
) -> list[ServiceBulletin]:
    """Async version of get_service_bulletins using a shared client."""
    return _parse_service_bulletins_response(await client.get(_service_bulletins_url(route_id)))

#: Seconds to wait between requests in get_routes, to be polite to the API.
ROUTES_COURTESY_WAIT_RANGE_S = (0.5, 1.5)

def get_routes(
    route_ids: Iterable[str],
    courtesy_wait_range_s: tuple[float, float] = ROUTES_COURTESY_WAIT_RANGE_S,
) -> list[Route]:
    """Fetch several routes with get_route, waiting a random courtesy interval
    between requests. A route that fails to fetch is skipped with a warning.
    """
    routes = []
    for i, route_id in enumerate(route_ids):
        if i:
            time.sleep(random.uniform(*courtesy_wait_range_s))
        try:
            routes.append(get_route(route_id))
        except httpx.HTTPError as e:
            warnings.warn(f"couldn't fetch route {route_id}: {e}", stacklevel=2)
    return routes
