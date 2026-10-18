import requests

from datetime import datetime

from aaata_bus.definitions import (
    BUSSES_FOR_ROUTE_URL, PREDICTIONS_FOR_BUS_URL,
)
from aaata_bus.schema import (
    BusesForRouteResponse, BusPrediction,
    Bus, PredictionsForBusResponse
)

def get_buses_for_route(route_id: str) -> list[Bus] | None:
    """Get all buses for a specific route.

    Args:
        route_id: The ID of the route to get buses for.

    Returns:
        A BusesResponse object containing the buses for the route.
    """
    url = BUSSES_FOR_ROUTE_URL.format(route_id=route_id)
    response = requests.get(url)
    response.raise_for_status()
    timestamp = datetime.now()
    busses_response = BusesForRouteResponse.from_api(response.json(), timestamp)

    # convert to list of buses
    return busses_response.buses

def get_predictions_for_bus(vehicle_id: str) -> list[BusPrediction] | None:
    """Get all predictions for a specific bus.

    Args:
        vehicle_id: The ID of the bus to get predictions for.

    Returns:
        A PredictionsResponse object containing the predictions for the bus.
    """
    url = PREDICTIONS_FOR_BUS_URL.format(vehicle_id=vehicle_id)
    response = requests.get(url)
    response.raise_for_status()
    predictions_response = PredictionsForBusResponse.from_api(response.json())

    # convert to list of predictions
    return predictions_response.predictions