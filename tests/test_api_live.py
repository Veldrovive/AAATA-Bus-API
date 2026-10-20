"""Smoke tests against the live TheRide API.

These need network access. When no buses are running (e.g. overnight) there is
nothing to check, so they warn instead of failing.
"""

import warnings

from aaata_bus.api import get_buses_for_route, get_predictions_for_bus, get_route, get_service_bulletins
from aaata_bus.schema import Bus, BusPrediction

LIVE_ROUTE = "22"


def test_live_get_buses_for_route():
    buses = get_buses_for_route(LIVE_ROUTE)
    assert isinstance(buses, list)
    if not buses:
        warnings.warn(f"No buses running on route {LIVE_ROUTE}; live API not fully checked")
        return
    assert isinstance(buses[0], Bus)


def test_live_get_predictions_for_bus():
    buses = get_buses_for_route(LIVE_ROUTE)
    assert buses is not None
    if not buses:
        warnings.warn(f"No buses running on route {LIVE_ROUTE}; live predictions not checked")
        return

    predictions = get_predictions_for_bus(buses[0].vehicle_id)
    assert isinstance(predictions, list)
    if not predictions:
        warnings.warn(f"No predictions for bus {buses[0].vehicle_id}; live predictions not checked")
        return
    assert isinstance(predictions[0], BusPrediction)


def test_live_get_route():
    # Route geometry is published regardless of whether buses are running.
    route = get_route(LIVE_ROUTE)
    assert route.route_id == LIVE_ROUTE
    assert route.name
    assert route.patterns
    assert route.patterns[0].stops


def test_live_get_service_bulletins():
    bulletins = get_service_bulletins(LIVE_ROUTE)
    assert isinstance(bulletins, list)
    if not bulletins:
        warnings.warn(f"No service bulletins for route {LIVE_ROUTE}; live bulletins not fully checked")
        return
    # Each is either system-wide or names the route.
    assert all(b.is_system_wide or LIVE_ROUTE in b.route_numbers for b in bulletins)
