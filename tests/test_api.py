from aaata_bus.api import get_buses_for_route, get_predictions_for_bus
from aaata_bus.schema import Bus, BusPrediction

def test_get_buses_for_route():
    buses = get_buses_for_route("22")
    assert isinstance(buses, list)
    assert len(buses) > 0
    assert isinstance(buses[0], Bus)

def test_get_predictions_for_bus():
    buses = get_buses_for_route("22")
    assert buses is not None

    predictions = get_predictions_for_bus(buses[0].vehicle_id)
    assert isinstance(predictions, list)
    assert len(predictions) > 0
    assert isinstance(predictions[0], BusPrediction)
