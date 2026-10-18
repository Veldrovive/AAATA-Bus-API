"""
Periodically saves route data to a jsonl file.
"""
import argparse
import random
import time
from datetime import datetime
from pydantic import BaseModel

from aaata_bus import (
    Bus,
    BusPrediction,
    get_buses_for_route,
    get_predictions_for_bus,
    DATA_PATH,
    ensure_dat,
)

DEFAULT_BUS_ROUTES = [22, 63, 65]
MINUTES_BETWEEN_SAVES = 5
COURTESY_WAIT_TIME_RANGE_S = (2, 5)

route_data_path = DATA_PATH / "route_prediction_data.jsonl"

def courtesy_wait():
    wait_time = random.uniform(COURTESY_WAIT_TIME_RANGE_S[0], COURTESY_WAIT_TIME_RANGE_S[1])
    time.sleep(wait_time)

class RouteData(BaseModel):
    route_id: str
    buses: list[Bus]
    predictions: dict[str, list[BusPrediction]]  # vehicle_id -> predictions

def save_route_data(route_id: int):
    busses = get_buses_for_route(str(route_id))
    if busses is None:
        return False

    predictions: dict[str, list[BusPrediction]] = {}
    for bus in busses:
        prediction = get_predictions_for_bus(bus.vehicle_id)
        if prediction is not None:
            predictions[bus.vehicle_id] = prediction
        
        courtesy_wait()

    route_data = RouteData(
        route_id=str(route_id),
        buses=busses,
        predictions=predictions
    )

    with open(route_data_path, "a") as f:
        f.write(route_data.model_dump_json() + "\n")
    return True
        
    
def main():
    parser = argparse.ArgumentParser(description="Periodically save AAATA bus route prediction data.")
    parser.add_argument(
        "--routes",
        type=int,
        nargs="+",
        default=DEFAULT_BUS_ROUTES,
        help="List of bus route IDs to track (e.g. --routes 22 63 65)",
    )
    parser.add_argument(
        "--interval-minutes",
        type=float,
        default=MINUTES_BETWEEN_SAVES,
        help=f"Minutes between saves (default: {MINUTES_BETWEEN_SAVES})",
    )
    args = parser.parse_args()

    ensure_dat()

    routes = args.routes
    interval_minutes = args.interval_minutes

    last_saved_time: float | None = None

    while True:
        current_time = time.time()
        if last_saved_time is not None and current_time - last_saved_time < interval_minutes * 60:
            time.sleep(1)
            continue

        last_saved_time = current_time
        print(f"Saving data for routes {routes} at {datetime.fromtimestamp(current_time)}")

        for route_id in routes:
            if not save_route_data(route_id):
                print(f"Failed to save data for route {route_id}")
            
            courtesy_wait()
        
        print(f"Saved data for routes {routes} at {datetime.fromtimestamp(current_time)}")

if __name__ == "__main__":
    main()
