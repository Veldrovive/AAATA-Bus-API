"""
Periodically saves route data to a jsonl file.
"""
import argparse
import random
import time
from datetime import datetime

from aaata_bus import (
    Bus,
    BusPrediction,
    RouteData,
    get_buses_for_route,
    get_predictions_for_bus,
    DATA_PATH,
    ensure_dat,
)

DEFAULT_BUS_ROUTES = [22, 63, 65]
MINUTES_BETWEEN_GENERAL_SAVES = 0.5
MINUTES_BETWEEN_PREDICTION_SAVES = 5
COURTESY_WAIT_TIME_RANGE_S = (2, 5)

route_data_path = DATA_PATH / "route_prediction_data.jsonl"

def courtesy_wait():
    wait_time = random.uniform(COURTESY_WAIT_TIME_RANGE_S[0], COURTESY_WAIT_TIME_RANGE_S[1])
    time.sleep(wait_time)

def save_route_data(route_id: int, collect_predictions: bool):
    busses = get_buses_for_route(str(route_id))
    if busses is None:
        return False

    predictions: dict[str, list[BusPrediction]] = {}
    if collect_predictions:
        for bus in busses:
            prediction = get_predictions_for_bus(bus.vehicle_id)
            if prediction is not None:
                predictions[bus.vehicle_id] = prediction

            courtesy_wait()

    route_data = RouteData(
        route_id=str(route_id),
        buses=busses,
        predictions=predictions,
        predictions_collected=collect_predictions,
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
        "--general-interval-minutes",
        type=float,
        default=MINUTES_BETWEEN_GENERAL_SAVES,
        help=f"Minutes between bus location saves (default: {MINUTES_BETWEEN_GENERAL_SAVES})",
    )
    parser.add_argument(
        "--prediction-interval-minutes",
        type=float,
        default=MINUTES_BETWEEN_PREDICTION_SAVES,
        help=(
            "Minutes between prediction saves; must be a whole multiple of "
            f"--general-interval-minutes (default: {MINUTES_BETWEEN_PREDICTION_SAVES})"
        ),
    )
    args = parser.parse_args()

    routes = args.routes
    bus_interval_s = args.general_interval_minutes * 60
    prediction_interval_s = args.prediction_interval_minutes * 60

    loops_per_prediction = round(prediction_interval_s / bus_interval_s)
    assert loops_per_prediction >= 1 and abs(loops_per_prediction * bus_interval_s - prediction_interval_s) < 1e-6, (
        f"Prediction interval ({args.prediction_interval_minutes} min) must be a whole multiple of "
        f"the general interval ({args.general_interval_minutes} min)"
    )

    ensure_dat()

    last_saved_time: float | None = None
    loop_count = 0

    while True:
        current_time = time.time()
        if last_saved_time is not None and current_time - last_saved_time < bus_interval_s:
            time.sleep(1)
            continue

        last_saved_time = current_time
        collect_predictions = loop_count % loops_per_prediction == 0
        loop_count += 1

        kind = "bus + prediction" if collect_predictions else "bus"
        print(f"Saving {kind} data for routes {routes} at {datetime.fromtimestamp(current_time)}")

        for route_id in routes:
            if not save_route_data(route_id, collect_predictions):
                print(f"Failed to save data for route {route_id}")

            courtesy_wait()

        print(f"Saved {kind} data for routes {routes} at {datetime.fromtimestamp(current_time)}")

if __name__ == "__main__":
    main()
