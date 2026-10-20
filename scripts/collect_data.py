"""
Periodically saves route data to a jsonl file.
"""
import argparse
import asyncio
import random
import time
from datetime import datetime

import httpx

from aaata_bus import (
    BusPrediction,
    RouteData,
    get_buses_for_route_async,
    get_predictions_for_bus_async,
    DATA_PATH,
    ensure_dat,
)

DEFAULT_BUS_ROUTES = [22, 63, 65]
MINUTES_BETWEEN_GENERAL_SAVES = 0.5
MINUTES_BETWEEN_PREDICTION_SAVES = 5
COURTESY_WAIT_TIME_RANGE_S = (2, 5)

route_data_path = DATA_PATH / "route_prediction_data.jsonl"

async def courtesy_wait():
    wait_time = random.uniform(COURTESY_WAIT_TIME_RANGE_S[0], COURTESY_WAIT_TIME_RANGE_S[1])
    await asyncio.sleep(wait_time)

async def save_route_data(client: httpx.AsyncClient, route_id: int, collect_predictions: bool):
    kind = "bus + prediction" if collect_predictions else "bus"
    requested_at = datetime.now()
    try:
        busses = await get_buses_for_route_async(client, str(route_id))
        if busses is None:
            print(f"Failed to save {kind} data for route {route_id}")
            return

        predictions: dict[str, list[BusPrediction]] = {}
        if collect_predictions:
            for bus in busses:
                prediction = await get_predictions_for_bus_async(client, bus.vehicle_id)
                if prediction is not None:
                    predictions[bus.vehicle_id] = prediction

                await courtesy_wait()
    except httpx.HTTPError as e:
        print(f"Failed to save {kind} data for route {route_id}: {e!r}")
        return

    route_data = RouteData(
        route_id=str(route_id),
        buses=busses,
        predictions=predictions,
        predictions_collected=collect_predictions,
        requested_at=requested_at,
    )

    with open(route_data_path, "a") as f:
        f.write(route_data.model_dump_json() + "\n")
    print(f"Saved {kind} data for route {route_id} at {datetime.now()}")


async def collect(routes: list[int], bus_interval_s: float, loops_per_prediction: int):
    # Keep references to running tasks so they aren't garbage collected mid-flight
    running_tasks: set[asyncio.Task] = set()
    # Prediction collection can outlast a prediction interval; never run two at once for a route
    prediction_tasks: dict[int, asyncio.Task] = {}
    loop_count = 0

    async with httpx.AsyncClient() as client:
        while True:
            loop_start = time.time()
            collect_predictions = loop_count % loops_per_prediction == 0
            loop_count += 1

            kind = "bus + prediction" if collect_predictions else "bus"
            print(f"Starting {kind} save for routes {routes} at {datetime.fromtimestamp(loop_start)}")

            for route_id in routes:
                route_collect_predictions = collect_predictions
                previous = prediction_tasks.get(route_id)
                if route_collect_predictions and previous is not None and not previous.done():
                    print(f"Predictions for route {route_id} still in progress; saving bus data only")
                    route_collect_predictions = False

                task = asyncio.create_task(save_route_data(client, route_id, route_collect_predictions))
                running_tasks.add(task)
                task.add_done_callback(running_tasks.discard)
                if route_collect_predictions:
                    prediction_tasks[route_id] = task

                await courtesy_wait()

            await asyncio.sleep(max(0.0, loop_start + bus_interval_s - time.time()))


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

    asyncio.run(collect(routes, bus_interval_s, loops_per_prediction))

if __name__ == "__main__":
    main()
