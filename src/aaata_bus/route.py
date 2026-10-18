from aaata_bus.api import get_buses_for_route, get_predictions_for_bus

class BusRoute:
    def __init__(self, route_id: str):
        self.route_id = route_id

        self.last_checked_buses_for_route_time = None